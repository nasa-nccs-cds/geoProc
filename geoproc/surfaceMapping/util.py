import xarray as xa
import geopandas as gpd
from math import floor, ceil
from typing import List, Union, Tuple, Optional

class TileLocator:

    @classmethod
    def floor10(cls, fval: float) -> int:
        return abs( int(floor(fval / 10.0)) * 10 )

    @classmethod
    def ceil10(cls, fval: float) -> int:
        return abs( int(ceil(fval / 10.0)) * 10 )

    @classmethod
    def unwrap(cls, coord: float) -> float:
        return coord if coord < 180 else coord - 360

    @classmethod
    def lon_label(cls, lon: float ) -> str:
        ulon = cls.unwrap( lon )
        if ulon < 0: return f"{cls.floor10(ulon):03d}W"
        else:        return f"{cls.floor10(ulon):03d}E"

    @classmethod
    def lat_label(cls, lat: float ) -> str:
        if lat > 0: return f"{cls.ceil10(lat):03d}N"
        else:       return f"{cls.ceil10(lat):03d}S"

    @classmethod
    def infer_tiles_xa( cls, array: xa.DataArray ) -> List[str]:
        print( f" --> infer_tiles_xa, attrs = {array.attrs}")
        x_coord = array.coords[array.dims[-1]].values
        y_coord = array.coords[array.dims[-2]].values
        x0, y0 = array.xgeo.project_to_geographic( x_coord[0], y_coord[0] )
        x1, y1 = array.xgeo.project_to_geographic(x_coord[-1], y_coord[-1])
        return cls.get_tiles( x0, x1, y0, y1 )

    @classmethod
    def infer_tiles_gpd( cls, series: gpd.GeoSeries ) -> List[str]:
        [xmin, ymin, xmax, ymax] = series.geometry.boundary.bounds.values[0]
        return cls.get_tiles( xmin, xmax, ymin, ymax )


    @classmethod
    def get_tiles( cls, xmin, xmax, ymin, ymax ) -> List[str]:
        xvals = { cls.lon_label( xmin ), cls.lon_label( xmax ) }
        yvals = { cls.lat_label( ymin ), cls.lat_label( ymax ) }
        results = [ f"{xval}{yval}" for xval in xvals for yval in yvals ]
        print( f"Inferring tiles {results} from xbounds = {[xmin,xmax]}, ybounds = {[ymin,ymax]}" )
        return results

    @classmethod
    def get_bounds(cls, array: xa.DataArray ) -> List:
        x_coord = array.coords[array.dims[-1]].values
        y_coord = array.coords[array.dims[-2]].values
        return [ x_coord[0], x_coord[-1], y_coord[0], y_coord[-1] ]

if __name__ == '__main__':
    lake_id = 334
    lake_mask_file = f"/Users/tpmaxwel/Dropbox/Tom/Data/Birkitt/MOD44W/2005/{lake_id}_2005.tif"
    array: xa.DataArray = xa.open_rasterio( lake_mask_file )
    print( TileLocator.infer_tiles_xa(array) )

    roi = "/Users/tpmaxwel/Dropbox/Tom/Data/Birkitt/saltLake/GreatSalt.shp"
    roi_bounds: gpd.GeoSeries = gpd.read_file(roi)
    print(TileLocator.infer_tiles_gpd(roi_bounds))