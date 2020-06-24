from typing import List, Union, Tuple, Optional
import xarray as xr
import pandas as pd
from geoproc.xext.xextension import XExtension
from geopandas import GeoDataFrame
import os, warnings
import numpy as np
from shapely.geometry import box, mapping
from geoproc.util.configuration import argfilter
import rioxarray, traceback
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling

@xr.register_dataarray_accessor('xrio')
class XRio(XExtension):
    """  This is an extension for xarray to provide an interface to rasterio capabilities """

    def __init__(self, xarray_obj: xr.DataArray):
        XExtension.__init__( self, xarray_obj )

    @classmethod
    def open( cls, iFile: int, filename: str, **kwargs )-> Optional[xr.DataArray]:
        mask = kwargs.pop("mask", None)
        kill_zombies = kwargs.pop( "kill_zombies", False )
        oargs = argfilter( kwargs, parse_coordinates = None, chunks = None, cache = None, lock = None )
        try:
            result: xr.DataArray = rioxarray.open_rasterio( filename, **oargs ).astype( np.dtype('f4') )
            band = kwargs.pop( 'band', -1 )
            if band >= 0:
                result = result.isel( band=band, drop=True )
            result.encoding = dict( dtype = str(np.dtype('f4')) )
            if mask is None: return result
            elif isinstance( mask, list ):
                return result.xrio.subset( iFile, mask[:2], mask[2:] )
            elif isinstance( mask, GeoDataFrame ):
                return result.xrio.clip( mask, **kwargs )
            else:
                raise Exception( f"Unrecognized mask type: {mask.__class__.__name__}")
        except Exception as err:
            print( f"XRio Error opening file {filename}: {err}")
            traceback.print_exc()
            if kill_zombies:
                print(f"Deleting erroneous file")
                os.remove( filename )
            return None

    def subset(self, iFile: int, xbounds: List, ybounds: List )-> xr.DataArray:
        from geoproc.surfaceMapping.util import TileLocator
        tile_bounds = TileLocator.get_bounds(self._obj)
        xbounds.sort(), ybounds.sort( reverse = (tile_bounds[2] > tile_bounds[3]) )
        if iFile == 0:
            print( f"Subsetting array with bounds {tile_bounds} by xbounds = {xbounds}, ybounds = {ybounds}")
        sel_args = { self._obj.dims[-1]: slice(*xbounds), self._obj.dims[-2]: slice(*ybounds) }
        return self._obj.sel(**sel_args)

    def clip(self, geodf: GeoDataFrame, **kwargs )-> xr.DataArray:
        cargs = argfilter( kwargs, all_touched = True, drop = True )
        mask_value = int( kwargs.pop( 'mask_value', 255  ) )
        self._obj.rio.set_nodata(mask_value)
        result = self._obj.rio.clip( geodf.geometry.apply(mapping), geodf.crs, **cargs )
        result.attrs['mask_value'] = mask_value
        result.encoding = self._obj.encoding
        return result

    @classmethod
    def load( cls, filePaths: Union[ str, List[str] ], **kwargs ) -> Union[ List[xr.DataArray], xr.DataArray ]:
        if isinstance( filePaths, str ): filePaths = [ filePaths ]
        result: xr.DataArray = None
        for iF, file in enumerate(filePaths):
            data_array: xr.DataArray = cls.open( iF, file, **kwargs )
            if data_array is not None:
                data_array = data_array.expand_dims( "time", 0)
                result = data_array if result is None else cls.concat( [ result, data_array ], axis='time' )
        return result


    @classmethod
    def load1( cls, filePaths: Union[ str, List[str] ], **kwargs ) -> Union[ List[xr.DataArray], xr.DataArray ]:
        if isinstance( filePaths, str ): filePaths = [ filePaths ]
        array_list: List[xr.DataArray] = []
        index_mask = np.full( [len(filePaths)], True )
        for iF, file in enumerate(filePaths):
            data_array: xr.DataArray = cls.open( iF, file, **kwargs )
            if data_array is not None:
                array_list.append( data_array )
            else:
                index_mask[iF] = False
        if (len(array_list) > 1):
            assert cls.mergable( array_list ), f"Attempt   to merge arrays with different shapes: {[ str(arr.shape) for arr in array_list ]}"
            result = cls.merge( array_list, index_mask=index_mask, **kwargs )
            return result
        return array_list if (len(array_list) > 1) else array_list[0]

    @classmethod
    def convert(self, source_file_path: str, dest_file_path: str, espg = 4236 ):
        dst_crs = f'EPSG:{espg}'

        with rasterio.open( source_file_path ) as src:
            print( f"PROFILE: {src.profile}" )
            src_crs = ''.join(src.crs.wkt.split())
            print(f" ---> CRS: {src_crs}")
            transform, width, height = calculate_default_transform( src_crs, dst_crs, src.width, src.height, *src.bounds )
            kwargs = src.meta.copy()
            kwargs.update({
                'crs': dst_crs,
                'transform': transform,
                'width': width,
                'height': height
            })

            with rasterio.open(dest_file_path, 'w', **kwargs) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src_crs,
                        dst_transform=transform,
                        dst_crs=dst_crs,
                        resampling=Resampling.nearest)

    @classmethod
    def merge( cls, data_arrays: List[xr.DataArray], **kwargs ) -> xr.DataArray:
        new_axis_name = kwargs.get('axis','time')
        index_mask = kwargs.get('index_mask', None )
        index = kwargs.get('index', None )
        new_axis_values = range( len(data_arrays) ) if index is None else index if index_mask is None else np.extract( index_mask, index )
        merge_coord = pd.Index( new_axis_values, name=new_axis_name )
        result: xr.DataArray =  xr.concat( data_arrays, merge_coord )
        return result

    @classmethod
    def concat( cls, data_arrays: List[xr.DataArray], **kwargs ) -> xr.DataArray:
        concat_axis_name = kwargs.get('axis','time')
        result: xr.DataArray =  xr.concat( data_arrays, concat_axis_name )
        print( f"Concat arrays along dim {concat_axis_name}, array dims = {data_arrays[0].dims}, {data_arrays[1].dims}, array shapes = {data_arrays[0].shape}, {data_arrays[1].shape}, Result array dims = {result.dims}, shape = {result.shape}")
        return result

    @classmethod
    def mergable(cls, arrays: List[xr.DataArray]) -> bool:
        for array in arrays:
            if array.shape != arrays[0].shape: return False
        return True
