@echo off
setlocal

rem Preliminary, unconditioned terrain-flow diagnostic. This is not an
rem executive hydraulic design and must be rerun after field structures,
rem culverts, roads and verified outlets are represented in the terrain.

set "ROOT=%CD%"
set "DEM=%ROOT%\dataset\DEM.tif"
set "OUT=%ROOT%\dataset\derived"

r.in.gdal input="%DEM%" output=terrain --overwrite
if errorlevel 1 exit /b 1

g.region raster=terrain
r.watershed elevation=terrain accumulation=flow_accumulation drainage=flow_direction basin=flow_basins stream=flow_streams threshold=867 convergence=5 memory=512 --overwrite
if errorlevel 1 exit /b 1

r.mapcalc expression="contributing_area_ha = abs(flow_accumulation) * area() / 10000.0" --overwrite
if errorlevel 1 exit /b 1

r.out.gdal -c input=flow_accumulation output="%OUT%\flow_accumulation_cells.tif" format=GTiff type=Float64 nodata=-9999 createopt="COMPRESS=DEFLATE" --overwrite
if errorlevel 1 exit /b 1
r.out.gdal -cf input=contributing_area_ha output="%OUT%\contributing_area_ha.tif" format=GTiff type=Float32 nodata=-9999 createopt="COMPRESS=DEFLATE" --overwrite
if errorlevel 1 exit /b 1
r.out.gdal -c input=flow_direction output="%OUT%\flow_direction.tif" format=GTiff type=Int16 nodata=0 createopt="COMPRESS=DEFLATE" --overwrite
if errorlevel 1 exit /b 1
r.out.gdal -c input=flow_basins output="%OUT%\flow_basins.tif" format=GTiff type=Int32 nodata=0 createopt="COMPRESS=DEFLATE" --overwrite
if errorlevel 1 exit /b 1
r.out.gdal -c input=flow_streams output="%OUT%\candidate_flow_paths_1ha.tif" format=GTiff type=Int32 nodata=-9999 createopt="COMPRESS=DEFLATE" --overwrite
if errorlevel 1 exit /b 1

endlocal
