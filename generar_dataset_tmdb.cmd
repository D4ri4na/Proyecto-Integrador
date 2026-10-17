@echo off
setlocal
cd /d "%~dp0"
set "MOVIES_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%MOVIES_PYTHON%" set "MOVIES_PYTHON=python"
echo Descarga de peliculas 2010-2026 desde TMDb.
echo La credencial se ingresa de forma oculta y no se guarda en el proyecto.
echo La primera ejecucion puede tardar; repetir este archivo reanuda la descarga.
"%MOVIES_PYTHON%" "%~dp0build_movies_dataset.py" --interactive --start 2010-01-01 --out "%~dp0datos_actualizados\movies_2010_2026.csv" --workdir "%~dp0datos_actualizados\cache_tmdb_v2" %*
set "MOVIES_EXIT=%ERRORLEVEL%"
echo.
if "%MOVIES_EXIT%"=="0" (echo Proceso completado.) else (echo Proceso no completado. Codigo de salida: %MOVIES_EXIT%)
pause
exit /b %MOVIES_EXIT%
