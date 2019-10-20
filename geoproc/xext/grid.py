# Adaped from https://github.com/snowman2/gazar.git
from csv import writer as csv_writer
import os
from affine import Affine
from typing import Dict, List, Tuple, Union
import numpy as np
from osgeo import gdal, gdalconst, ogr, osr
from pyproj import Proj, transform
import xarray as xr
import utm

gdal.UseExceptions()

def project_to_geographic(x_coord, y_coord, osr_projetion):
    """Project point to EPSG:4326

    Parameters
    ----------
    x_coord : float
        The point x-coordinate.
    y_coord:  float
        The point y-coordinate.
    osr_projetion:  :func:`osr.SpatialReference`
        The projection for the point.

    Returns
    -------
    :obj:`tuple`
        The projected point coordinates.
        (x_coord, y_coord)
    """
    # Make sure projected into global projection
    sp_ref = osr.SpatialReference()
    sp_ref.ImportFromEPSG(4326)
    trans = osr.CoordinateTransformation(osr_projetion, sp_ref)
    return trans.TransformPoint(x_coord, y_coord)[:2]


class GDALGrid(object):
    """
    Wrapper for :func:`gdal.Dataset` with
    :func:`osr.SpatialReference` object.

    Parameters
    ----------
    grid_file : :obj: str` or :func:`gdal.Dataset`
        The grid file to be wrapped.
    prj_file : :obj:`str`, optional
        Path to projection file.

    """
    def __init__(self, grid_file, prj_file=None):
        if isinstance(grid_file, gdal.Dataset):
            self.dataset = grid_file
        else:
            self.dataset = gdal.Open(grid_file, gdalconst.GA_ReadOnly)

        # set projection object
        if prj_file is not None:
            self.projection = osr.SpatialReference()
            with open(prj_file) as pro_file:
                self.projection.ImportFromWkt(pro_file.read())
        else:
            self.projection = osr.SpatialReference()
            self.projection.ImportFromWkt(self.dataset.GetProjection())

        # set affine from geotransform
        self.affine = Affine.from_gdal(*self.dataset.GetGeoTransform())

    @property
    def geotransform(self):
        """:obj:`tuple`: The geotransform for the dataset."""
        return self.dataset.GetGeoTransform()

    @property
    def x_size(self):
        """int: size of x dimensions"""
        return self.dataset.RasterXSize

    @property
    def y_size(self):
        """int: size of y dimensions"""
        return self.dataset.RasterYSize

    @property
    def num_bands(self):
        """int: number of bands in raster"""
        return self.dataset.RasterCount

    @property
    def wkt(self):
        """:obj:`str`:WKT projection string"""
        return self.projection.ExportToWkt()

    @property
    def proj4(self):
        """:obj:`str`:proj4 string"""
        return self.projection.ExportToProj4()

    @property
    def proj(self):
        """func:`pyproj.Proj`: Proj4 object"""
        return Proj(self.proj4)

    @property
    def epsg(self):
        """:obj:`str`: EPSG code"""
        try:
            # identify EPSG code where applicable
            self.projection.AutoIdentifyEPSG()
        except RuntimeError:
            pass
        return self.projection.GetAuthorityCode(None)

    def bounds(self, as_geographic=False, as_utm=False, as_projection=None):
        """Returns bounding coordinates for the dataset.

        Parameters
        ----------
        as_geographic : bool, optional
            If True, this will return the bounds in EPSG:4326.
            Default is False.
        as_utm:  bool, optional
            If True, it will attempt to find the UTM zone and
            will return bounds in that UTM zone.
        as_projection:  :func:`osr.SpatialReference`, optional
            Output projection for bounds.

        Returns
        -------
        :obj:`tuple`
            (x_min, x_max, y_min, y_max)
            Bounds for the grid in the format

        """
        new_proj = None

        if as_geographic:
            new_proj = osr.SpatialReference()
            new_proj.ImportFromEPSG(4326)
        elif as_utm:
            new_proj = self.get_utm_proj()
        elif as_projection:
            new_proj = as_projection

        if new_proj is not None:
            ggrid = self.to_projection(new_proj)
            return ggrid.bounds()

        x_min, y_min = self.affine * (0, self.dataset.RasterYSize)
        x_max, y_max = self.affine * (self.dataset.RasterXSize, 0)
        return x_min, x_max, y_min, y_max

    def get_utm_proj(self) -> osr.SpatialReference:
        x_min, y_min = self.affine * (0, self.dataset.RasterYSize)
        x_max, y_max = self.affine * (self.dataset.RasterXSize, 0)

        lon_min, lat_max = project_to_geographic(x_min, y_max, self.projection)
        lon_max, lat_min = project_to_geographic(x_max, y_min, self.projection)

        latitude = (lat_min + lat_max) / 2.0
        longitude = (lon_min + lon_max) / 2.0

        utm_centroid_info = utm.from_latlon(latitude, longitude)
        zone_number, zone_letter = utm_centroid_info[2:]

        # METHOD USING SetUTM. Not sure if better/worse
        sp_ref = osr.SpatialReference()

        south_string = ''
        if zone_letter < 'N':
            south_string = ', +south'
        proj4_utm_string = ('+proj=utm +zone={zone_number}{zone_letter}'
                            '{south_string} +ellps=WGS84 +datum=WGS84 '
                            '+units=m +no_defs') \
            .format(zone_number=abs(zone_number),
                    zone_letter=zone_letter,
                    south_string=south_string)
        ret_val = sp_ref.ImportFromProj4(proj4_utm_string)
        if ret_val == 0:
            north_zone = True
            if zone_letter < 'N':
                north_zone = False
            sp_ref.SetUTM(abs(zone_number), north_zone)

        sp_ref.AutoIdentifyEPSG()
        return sp_ref


    def pixel2coord(self, col, row):
        """Returns global coordinates to pixel center using base-0 raster index.

        Parameters
        ----------
        col: int
            The 0-based column index.
        row:  int
            The 0-based row index.

        Returns
        -------
        :obj:`tuple`
            (x_coord, y_coord) - The x, y coordinate of the pixel
            center in the dataset's projection.

        """
        if col >= self.x_size:
            raise IndexError("Column index out of bounds...")
        if row >= self.y_size:
            raise IndexError("Row index is out of bounds ...")
        return self.affine * (col + 0.5, row + 0.5)

    def coord2pixel(self, x_coord, y_coord):
        """Returns base-0 raster index using global coordinates to pixel center

        Parameters
        ----------
        x_coord: float
            The projected x coordinate of the cell center.
        y_coord:  float
            The projected y coordinate of the cell center.

        Returns
        -------
        :obj:`tuple`
            (col, row) - The 0-based column and row index of the pixel.
        """
        col, row = ~self.affine * (x_coord, y_coord)
        if col > self.x_size or col < 0:
            raise IndexError("Longitude {0} is out of bounds ..."
                             .format(x_coord))
        if row > self.y_size or row < 0:
            raise IndexError("Latitude {0} is out of bounds ..."
                             .format(y_coord))

        return int(col), int(row)

    def pixel2lonlat(self, col, row):
        """Returns latitude and longitude to pixel center using base-0 raster index

        Parameters
        ----------
        col: int
            The 0-based column index.
        row:  int
            The 0-based row index.

        Returns
        -------
        :obj:`tuple`
            (longitude, latitude) - The lat, lon of the pixel
            center in the dataset's projection.
        """
        x_coord, y_coord = self.pixel2coord(col, row)
        longitude, latitude = project_to_geographic(x_coord, y_coord,
                                                    self.projection)
        return longitude, latitude

    def lonlat2pixel(self, longitude, latitude):
        """Returns base-0 raster index using longitude and latitude of pixel center

        Parameters
        ----------
        longitude: float
            The longitude of the cell center.
        latitude:  float
            The latitude of the cell center.

        Returns
        -------
        :obj:`tuple`
            (col, row) - The 0-based column and row index of the pixel.
        """
        sp_ref = osr.SpatialReference()
        sp_ref.ImportFromEPSG(4326)  # geographic
        transx = osr.CoordinateTransformation(sp_ref, self.projection)
        x_coord, y_coord = transx.TransformPoint(longitude, latitude)[:2]
        return self.coord2pixel(x_coord, y_coord)

    @property
    def x_coords(self) -> np.array:
        """ Returns x coordinate array representing the grid. """
        x_coords, _ = (np.arange(self.x_size) + 0.5, np.zeros(self.x_size) + 0.5) * self.affine
        return x_coords

    @property
    def y_coords(self) -> np.array:
        """ Returns y coordinate array representing the grid. """
        _, y_coords = (np.zeros(self.y_size) + 0.5, np.arange(self.y_size) + 0.5) * self.affine
        return y_coords

    @property
    def latlon(self) -> Tuple[np.array,np.array]:
        """Returns ( latitude, longitude ) array tuple representing the grid. """
        x_2d_coords, y_2d_coords = np.meshgrid(self.x_coords, self.y_coords)
        proj_lons, proj_lats = transform(self.proj, Proj(init='epsg:4326'), x_2d_coords, y_2d_coords)
        return proj_lats, proj_lons

    def np_array(self, band: int = 1, masked: bool =True) -> np.array:
        """Returns the raster band as a numpy array.  If band < 0,  it will return all of the data as a 3D array. """
        if band < 0:
            grid_data = self.dataset.ReadAsArray()
        else:
            raster_band = self.dataset.GetRasterBand(band)
            grid_data = raster_band.ReadAsArray()
            nodata_value = raster_band.GetNoDataValue()
            if nodata_value is not None and masked:
                return np.ma.array(data=grid_data, mask=(grid_data == nodata_value))
        return np.array(grid_data)

    def xarray( self, name: str, band: int = 1, masked: bool = True ) -> xr.DataArray:
        xy_data = self.np_array( band, masked )
        return xr.DataArray( xy_data, name=name, coords = dict( x=self.x_coords, y=self.y_coords ), dims = [ "y", "x" ], attrs=dict( crs=self.projection.ExportToProj4() ) )

    def get_val(self, x_pixel, y_pixel, band=1):
        """Returns value of raster

        Parameters
        ----------
        x_pixel: int
            X pixel location (0-based).
        y_pixel: int
            Y pixel location (0-based).
        band: int, optional
            Band number (1-based). Default is 1.

        Returns
        -------
        object dtype
        """
        return self.dataset.GetRasterBand(band)\
                   .ReadAsArray(x_pixel, y_pixel, 1, 1)[0][0]

    def get_val_latlon(self, longitude, latitude, band=1):
        """Returns value of raster from a latitude and longitude point.

        Parameters
        ----------
        longitude: float
            The longitude of the cell center.
        latitude:  float
            The latitude of the cell center.
        band: int, optional
            Band number (1-based). Default is 1.

        Returns
        -------
        object dtype
        """
        x_pixel, y_pixel = self.lonlat2pixel(longitude, latitude)
        return self.get_val(x_pixel, y_pixel, band)

    def get_val_coord(self, x_coord, y_coord, band=1):
        """Returns value of raster from a projected coordinate point.

        Parameters
        ----------
        x_coord: float
            The projected x coordinate of the cell center.
        y_coord:  float
            The projected y coordinate of the cell center.
        band: int, optional
            Band number (1-based). Default is 1.

        Returns
        -------
        object dtype
        """
        x_pixel, y_pixel = self.coord2pixel(x_coord, y_coord)
        return self.get_val(x_pixel, y_pixel, band)

    def write_prj(self, out_projection_file, esri_format=False):
        """Writes projection file.

        Parameters
        ----------
        out_projection_file:  :obj:`str`
            Output path for file.
        esri_format: bool, optional
            If True, it will convert the projection string to
            the Esri format. Default is False.
        """
        if esri_format:
            self.projection.MorphToESRI()
        with open(out_projection_file, 'w') as prj_file:
            prj_file.write(self.wkt)
            prj_file.close()

    def to_polygon(self,
                   out_shapefile,
                   band=1,
                   fieldname='DN',
                   self_mask=None):
        """Converts the raster to a polygon.

        Based on:
        ---------
        https://svn.osgeo.org/gdal/trunk/gdal/swig/python/scripts
            /gdal_polygonize.py

        https://stackoverflow.com/questions/25039565
            /create-shapefile-from-tif-file-using-gdal

        Parameters
        ----------
        out_shapefile:  :obj:`str`
            Output path for shapefile.
        band: int, optional
            Band number (1-based). Default is 1.
        fieldname: str, optional
            Name of the output field. Defailt is 'DN'.
        self_mask: bool, optional
            If True, will use self as mask. Default is None.
        """

        raster_band = self.dataset.GetRasterBand(band)
        if self_mask:
            self_mask = raster_band
        else:
            self_mask = None

        drv = ogr.GetDriverByName("ESRI Shapefile")
        dst_ds = drv.CreateDataSource(out_shapefile)
        dst_layername = os.path.splitext(os.path.basename(out_shapefile))[0]
        dst_layer = dst_ds.CreateLayer(dst_layername, srs=self.projection)

        # mapping between gdal type and ogr field type
        type_mapping = {gdal.GDT_Byte: ogr.OFTInteger,
                        gdal.GDT_UInt16: ogr.OFTInteger,
                        gdal.GDT_Int16: ogr.OFTInteger,
                        gdal.GDT_UInt32: ogr.OFTInteger,
                        gdal.GDT_Int32: ogr.OFTInteger,
                        gdal.GDT_Float32: ogr.OFTReal,
                        gdal.GDT_Float64: ogr.OFTReal,
                        gdal.GDT_CInt16: ogr.OFTInteger,
                        gdal.GDT_CInt32: ogr.OFTInteger,
                        gdal.GDT_CFloat32: ogr.OFTReal,
                        gdal.GDT_CFloat64: ogr.OFTReal}

        fld = ogr.FieldDefn(fieldname, type_mapping[raster_band.DataType])
        dst_layer.CreateField(fld)

        gdal.Polygonize(raster_band,
                        self_mask,
                        dst_layer,
                        0,
                        [],
                        callback=None)

    def to_projection(self, dst_proj: osr.SpatialReference,  resampling=gdalconst.GRA_NearestNeighbour) -> "GDALGrid":
        """ Reproject dataset to new projection.  """
        return gdal_reproject(self.dataset, src_srs=self.projection,  dst_srs=dst_proj,  resampling=resampling,  as_gdal_grid=True)

    def to_tif( self, file_path: str ):
        """Write out as geotiff."""
        drv = gdal.GetDriverByName('GTiff')
        drv.CreateCopy(file_path, self.dataset)

    def _to_ascii(self, header_string, file_path, band, print_nodata=True):
        """Writes data to ascii file"""
        if print_nodata:
            nodata_value = self.dataset.GetRasterBand(band).GetNoDataValue()
            if nodata_value is not None:
                header_string += "NODATA_value {0}\n".format(nodata_value)

        with open(file_path, 'w') as out_ascii_grid:
            out_ascii_grid.write(header_string)
            grid_writer = csv_writer(out_ascii_grid,
                                     delimiter=" ")
            grid_writer.writerows(self.np_array(band, masked=False))

    def to_grass_ascii(self, file_path: str, band: int =1, print_nodata: bool =True):
        """ Writes data to GRASS ASCII file format. """
        west_bound, east_bound, south_bound, north_bound = self.bounds()
        header_string = u"north: {0:.9f}\n".format(north_bound)
        header_string += "south: {0:.9f}\n".format(south_bound)
        header_string += "east: {0:.9f}\n".format(east_bound)
        header_string += "west: {0:.9f}\n".format(west_bound)
        header_string += "rows: {0}\n".format(self.y_size)
        header_string += "cols: {0}\n".format(self.x_size)
        self._to_ascii(header_string, file_path, band, print_nodata)

    def to_arc_ascii(self, file_path: str, band: int =1, print_nodata: bool =True):
        """Writes data to Arc ASCII file format. """
        bounds = self.bounds()
        west_bound = bounds[0]
        south_bound = bounds[2]
        cellsize = (self.geotransform[1] - self.geotransform[-1]) / 2.0
        header_string = u"ncols {0}\n".format(self.x_size)
        header_string += "nrows {0}\n".format(self.y_size)
        header_string += "xllcorner {0}\n".format(west_bound)
        header_string += "yllcorner {0}\n".format(south_bound)
        header_string += "cellsize {0}\n".format(cellsize)
        self._to_ascii(header_string, file_path, band, print_nodata)


def load_raster(grid):
    """
    Load in a raster as a :func:`~GDALGrid`.

    Parameters
    ----------
        grid: :obj:`str` or :func:`gdal.Dataset` or :func:`~GDALGrid`
            The raster to be loaded in.
    Returns
    -------
    :func:`~GDALGrid`
    """
    if isinstance(grid, gdal.Dataset):
        src = grid
        src_proj = src.GetProjection()
    elif isinstance(grid, GDALGrid):
        src = grid.dataset
        src_proj = grid.wkt
    else:
        src = gdal.Open(grid, gdalconst.GA_ReadOnly)
        src_proj = src.GetProjection()

    return src, src_proj


def resample_grid(original_grid,
                  match_grid,
                  to_file=False,
                  output_datatype=None,
                  resample_method=gdalconst.GRA_Average,
                  as_gdal_grid=False):
    """
    This function resamples a grid and outputs the result to a file.

    Based on: http://stackoverflow.com/questions/10454316/how-to-project-and-
        resample-a-grid-to-match-another-grid-with-gdal-python

    Parameters
    ----------
        original_grid: :obj:`str` or :func:`gdal.Dataset` or :func:`~GDALGrid`
            The original grid dataset.
        match_grid: :obj:`str` or :func:`gdal.Dataset` or :func:`~GDALGrid`
            The grid to match.
        to_file: :obj:`str` or bool, optional
            Default is False, which returns an in memory grid.
            If :obj:`str`, it writes to file.
        output_datatype: :func:`osgeo.gdalconst`, optional
            A valid datatype from gdalconst (Ex. gdalconst.GDT_Float32).
        resample_method: :func:`osgeo.gdalconst`, optional
            A valid resample method from gdalconst.
            Default is gdalconst.GRA_Average.
        as_gdal_grid: bool, optional
            Return as :func:`~GDALGrid`. Default is False.

    Returns
    -------
    None or :func:`gdal.Dataset` or :func:`~GDALGrid`
        If `to_file` is a :obj:`str`, then it returns None.
        Otherwise, if `to_file` is False then it returns a
        :func:`gdal.Dataset` unless `as_gdal_grid` is True.
        Then, it returns :func:`~GDALGrid`.

    """
    # Source of the data
    src, src_proj = load_raster(original_grid)

    # ensure output datatype is set
    if output_datatype is None:
        output_datatype = src.GetRasterBand(1).DataType

    # Grid to use to extract subset and match
    match_ds, match_proj = load_raster(match_grid)
    match_geotrans = match_ds.GetGeoTransform()

    if not to_file:
        # in memory raster
        dst_driver = gdal.GetDriverByName('MEM')
        dst_path = ""
    else:
        # geotiff
        dst_driver = gdal.GetDriverByName('GTiff')
        dst_path = to_file

    dst = dst_driver.Create(dst_path,
                            match_ds.RasterXSize,
                            match_ds.RasterYSize,
                            src.RasterCount,
                            output_datatype)

    dst.SetGeoTransform(match_geotrans)
    dst.SetProjection(match_proj)

    for band_i in range(1, dst.RasterCount + 1):
        nodata_value = src.GetRasterBand(band_i).GetNoDataValue()
        if not nodata_value:
            nodata_value = -9999
        dst.GetRasterBand(band_i).SetNoDataValue(nodata_value)

    # extract subset and resample grid
    gdal.ReprojectImage(src, dst,
                        src_proj,
                        match_proj,
                        resample_method)

    if not to_file:
        if as_gdal_grid:
            return GDALGrid(dst)
        return dst
    del dst
    return None


def gdal_reproject(src,
                   dst=None,
                   src_srs=None,
                   dst_srs=None,
                   epsg=None,
                   error_threshold=0.125,
                   resampling=gdalconst.GRA_NearestNeighbour,
                   as_gdal_grid=False):
    """
    Reproject a raster image.

    Based on: https://github.com/OpenDataAnalytics/
            gaia/blob/master/gaia/geo/gdal_functions.py

    Parameters
    ----------
        src: :obj:`str` or :func:`gdal.Dataset` or :func:`~GDALGrid`
            The source image.
        dst: :obj:`str`, optional
            The filepath of the output image to write to.
        src_srs: :func:`osr.SpatialReference`, optional
            The source image projection.
        dst_srs: :func:`osr.SpatialReference`, optional
            The destination projection. If not provided,
            the code will use `epsg`.
        epsg: int, optional
            The EPSG code to reproject to. If not provided,
            the code will use `dst_srs`.
        error_threshold: float, optional
            Default is 0.125 (same as gdalwarp commandline).
        resampling: :func:`osgeo.gdalconst`
            Method to use for resampling. Default method is
            `gdalconst.GRA_NearestNeighbour`.
        as_gdal_grid: bool, optional
            Return as :func:`~GDALGrid`. Default is False.

    Returns
    -------
    :func:`gdal.Dataset` or :func:`~GDALGrid`
        By default, it returns `gdal.Dataset`.
        It will return :func:`~GDALGrid` if `as_gdal_grid` is True.
    """
    # Open source dataset
    src_ds = load_raster(src)[0]

    # Define target SRS
    if dst_srs is None:
        dst_srs = osr.SpatialReference()
        dst_srs.ImportFromEPSG(int(epsg))

    dst_wkt = dst_srs.ExportToWkt()

    # Resampling might be passed as a string
    if not isinstance(resampling, int):
        resampling = getattr(gdal, resampling)

    src_wkt = None
    if src_srs is not None:
        src_wkt = src_srs.ExportToWkt()

    # Call AutoCreateWarpedVRT() to fetch default values
    # for target raster dimensions and geotransform
    reprojected_ds = gdal.AutoCreateWarpedVRT(src_ds,
                                              src_wkt,
                                              dst_wkt,
                                              resampling,
                                              error_threshold)

    # Create the final warped raster
    if dst:
        gdal.GetDriverByName('GTiff').CreateCopy(dst, reprojected_ds)
    if as_gdal_grid:
        return GDALGrid(reprojected_ds)
    return reprojected_ds

if __name__ == '__main__':
    import xarray as xr

    CreateIPServer = "https://dataserver.nccs.nasa.gov/thredds/dodsC/bypass/CREATE-IP/"
    CIP_addresses = {
        "merra2": CreateIPServer + "/reanalysis/MERRA2/mon/atmos/{}.ncml",
        "merra": CreateIPServer + "/reanalysis/MERRA/mon/atmos/{}.ncml",
        "ecmwf": CreateIPServer + "/reanalysis/ECMWF/mon/atmos/{}.ncml",
        "cfsr": CreateIPServer + "/reanalysis/CFSR/mon/atmos/{}.ncml",
        "20crv": CreateIPServer + "/reanalysis/20CRv2c/mon/atmos/{}.ncml",
        "jra": CreateIPServer + "/reanalysis/JMA/JRA-55/mon/atmos/{}.ncml",
    }
    def CIP( model: str, varName: str) -> str:
        return CIP_addresses[model.lower()].format(varName)

    data_address = CIP( "merra2", "tas" )

    dataset = xr.open_dataset( data_address )
    variable: xr.DataArray = dataset["tas"]
    print( variable.geoproc.dx )