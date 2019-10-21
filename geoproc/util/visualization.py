import matplotlib.animation as animation
from matplotlib.figure import Figure
from geoproc.util.configuration import ConfigurableObject, Region
from matplotlib.colors import LinearSegmentedColormap, Normalize
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image
from typing import Dict, List, Tuple, Union
import os, time, sys
import xarray as xr

class TilePlotter(ConfigurableObject):

    def __init__(self, **kwargs ):
        ConfigurableObject.__init__( self, **kwargs )
        colors = self.getParameter( "colors", [(0, 0, 0), (0.5, 1, 0.25), (0, 0, 1), (1, 1, 0)] )
        self.setColormap( colors )

    def setColormap(self, colors: List[Tuple[float,float,float]] ):
        self.norm = Normalize( 0,len(colors) )
        self.cm = LinearSegmentedColormap.from_list( "geoProc-TilePlotter", colors, N=len(colors) )

    def plot(self, axes, data_arrays: Union[xr.DataArray,List[xr.DataArray]], timeIndex = -1 ):
        print("Plotting tile")
        if not isinstance(data_arrays, list): data_arrays = [data_arrays]
        if timeIndex >= 0:
            axes.imshow( data_arrays[timeIndex].values, cmap=self.cm, norm=self.norm )
        else:
            if len( data_arrays ) == 1:
                axes.imshow( data_arrays[0].values, cmap=self.cm, norm=self.norm )
            else:
                da: xr.DataArray = self.time_merge( data_arrays )
                result = da[0].copy()
                result = result.where( result == 0, 0 )
                land = ( da == 1 ).sum( axis=0 )
                perm_water = ( da == 2 ).sum( axis=0 )
                print( "Computed masks" )
                result = result.where( land == 0, 1 )
                result = result.where( perm_water == 0, 2 )
                axes.imshow( result.values, cmap=self.cm, norm=self.norm )

class ArrayAnimation(ConfigurableObject):

    def __init__(self, **kwargs ):
        ConfigurableObject.__init__( self, **kwargs )

    def create_file_animation(self,  files: List[str], savePath: str = None, overwrite = False ) -> animation.TimedAnimation:
        from geoproc.xext.xgeo import XGeo
        bbox: Region = self.getParameter("bbox")
        data_arrays: List[xr.DataArray] = XGeo.loadRasterFiles(files, region=bbox)
        return self.create_animation( data_arrays, savePath, overwrite )

    def create_array_animation(self,  data_array: xr.DataArray, savePath: str = None, overwrite = False ) -> animation.TimedAnimation:
        data_arrays: List[xr.DataArray] = [  data_array[iT] for iT in range(data_array.shape[0]) ]
        return self.create_animation( data_arrays, savePath, overwrite )

    def create_animation( self, data_arrays: List[xr.DataArray], savePath: str = None, overwrite = False ) -> animation.TimedAnimation:
        images = []
        t0 = time.time()
        colors = [(0, 0, 0), (0.05, 0.4, 0.2), (1, 1, 0), (0, 1, 1)]   # (0.15, 0.3, 0.5)
        norm = Normalize(0,len(colors))
        cm = LinearSegmentedColormap.from_list( "lake-map", colors, N=4 )
        fps = self.getParameter( "fps", 1 )
        roi: Region = self.getParameter("roi")
        print("\n Executing create_array_animation ")
        figure, axes = plt.subplots() if roi is None else plt.subplots(1,2)

        if roi is  None:
            for da in data_arrays:
                im: Image = axes.imshow( da.values, animated=True, cmap=cm, norm=norm )
                images.append([im])
        else:
            for da in data_arrays:
                im0: Image = axes[0].imshow( da.values, animated=True, cmap=cm, norm=norm  )
                im1: Image = axes[1].imshow( da[ roi.origin[0]:roi.bounds[0], roi.origin[1]:roi.bounds[1] ], animated=True, cmap=cm, norm=norm )
                images.append( [im0,im1] )

            rect = patches.Rectangle( roi.origin, roi.size, roi.size, linewidth=1, edgecolor='r', facecolor='none')
            axes[0].add_patch(rect)

        anim = animation.ArtistAnimation( figure, images, interval=1000.0/fps )

        if savePath is not None:
            if ( overwrite or not os.path.exists( savePath )):
                anim.save( savePath, fps=fps )
                print( f" Animation saved to {savePath}" )
            else:
                print( f" Animation file already exists at '{savePath}'', set 'overwrite = True'' if you wish to overwrite it." )
        print(f" Completed create_array_animation in {time.time()-t0:.3f} seconds" )
        plt.show()
        return anim

    def getDataSubset( self, data_arrays: List[xr.DataArray], frameIndex: int, bin_size: 8, roi: Region ):
        results = []
        for iFrame in range(frameIndex,frameIndex+bin_size):
            da = data_arrays[ min( iFrame, len(data_arrays)-1 ) ]
            results.append( da[ roi.origin[0]:roi.bounds[0], roi.origin[1]:roi.bounds[1] ] )
        return results

    def create_watermap_diag_animation( self, title: str, data_arrays: List[xr.DataArray], savePath: str = None, overwrite = False ) -> animation.TimedAnimation:
        from geoproc.surfaceMapping.lakes import WaterMapGenerator
        images = []
        t0 = time.time()
        colors = [(0, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1)]
        norm = Normalize(0,4)
        cm = LinearSegmentedColormap.from_list( "lake-map", colors, N=4 )
        fps = self.getParameter( "fps", 0.5 )
        roi: Region = self.getParameter("roi")
        print("\n Executing create_array_animation ")
        figure, axes = plt.subplots(2,2)
        waterMapGenerator = WaterMapGenerator()
        water_maps = [ {}, {}, {} ]
        figure.suptitle(title, fontsize=16)

        anim_running = True
        def onClick(event):
            nonlocal anim_running
            if anim_running:
                anim.event_source.stop()
                anim_running = False
            else:
                anim.event_source.start()
                anim_running = True

        for frameIndex in range( len(data_arrays) ):
            waterMaskIndex = frameIndex // 8
            da0 = data_arrays[frameIndex]
            waterMask11 = water_maps[0].setdefault( waterMaskIndex, waterMapGenerator.get_water_mask( self.getDataSubset(data_arrays, frameIndex, 8, roi ), 0.5, 1 ) )
            waterMask12 = water_maps[1].setdefault( waterMaskIndex, waterMapGenerator.get_water_mask( self.getDataSubset(data_arrays, frameIndex, 8, roi ), 0.5, 2 ) )
            waterMask13 = water_maps[2].setdefault( waterMaskIndex, waterMapGenerator.get_water_mask( self.getDataSubset(data_arrays, frameIndex, 8, roi ), 0.5, 3 ) )
#            im0: Image = axes[0].imshow(da.values, animated=True, cmap=cm, norm=norm  )
            axes[0,0].title.set_text('raw data');           axes[0, 0].set_yticklabels([]); axes[0, 0].set_xticklabels([])
            im0: Image = axes[0,0].imshow( da0[ roi.origin[0]:roi.bounds[0], roi.origin[1]:roi.bounds[1] ], animated=True, cmap=cm, norm=norm )
            axes[0, 1].title.set_text('minw: 1');  axes[0, 1].set_yticklabels([]); axes[0, 1].set_xticklabels([])
            im1: Image = axes[0,1].imshow( waterMask11, animated=True, cmap=cm, norm=norm )
            axes[1, 0].title.set_text('minw: 2');  axes[1, 0].set_yticklabels([]); axes[1, 0].set_xticklabels([])
            im2: Image = axes[1,0].imshow( waterMask12, animated=True, cmap=cm, norm=norm)
            axes[1, 1].title.set_text('minw: 3');  axes[1, 1].set_yticklabels([]); axes[1, 1].set_xticklabels([])
            im3: Image = axes[1,1].imshow( waterMask13, animated=True,  cmap=cm, norm=norm)
            images.append( [im0,im1,im2,im3] )

#        rect = patches.Rectangle( roi.origin, roi.size, roi.size, linewidth=1, edgecolor='r', facecolor='none')
#        axes[0].add_patch(rect)
        figure.canvas.mpl_connect('button_press_event', onClick)
        anim = animation.ArtistAnimation( figure, images, interval=1000.0/fps, repeat_delay=1000)

        if savePath is not None:
            if ( overwrite or not os.path.exists( savePath )):
                anim.save( savePath, fps=fps )
                print( f" Animation saved to {savePath}" )
            else:
                print( f" Animation file already exists at '{savePath}'', set 'overwrite = True'' if you wish to overwrite it." )
        print(f" Completed create_array_animation in {time.time()-t0:.3f} seconds" )
        plt.tight_layout()
        plt.show()
        return anim

    def animateGifs(self, gifList: List[str] ):
        images = [ Image.open(gifFile).convert('RGB') for gifFile in gifList ]
        nImages = len( images )
        nRows = nImages // 3
        nCols = nImages // nRows
        figure, axes = plt.subplots( nRows, nCols )




if __name__ == '__main__':
    from geoproc.data.mwp import MWPDataManager

    t0 = time.time()
    locations = [ "120W050N", "100W040N" ]
    products = [ "1D1OS", "2D2OT" , "3D3OT" ]
    DATA_DIR = "/Users/tpmaxwel/Dropbox/Tom/Data/Birkitt"
    location: str = locations[0]
    product = products[0]
    year = 2019
    download = False
    roi = Region( [250,250], 20 )
    bbox = Region([1750, 1750], 500 )
    savePath = DATA_DIR + "/watermap_diagnostic_animation.gif"
    fps = 1.0

    dataMgr = MWPDataManager(DATA_DIR, "https://floodmap.modaps.eosdis.nasa.gov/Products")
    dataMgr.setDefaults( product=product, download=download, year=2019, start_day=1, end_day=365, bbox=bbox )
    data_arrays = dataMgr.get_tile_data(location)

    animator = ArrayAnimation( roi=roi, fps=fps )
    anim = animator.create_watermap_diag_animation( f"{product} @ {location}", data_arrays, savePath, True )
