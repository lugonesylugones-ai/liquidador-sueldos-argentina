@echo off
rem Arranca el liquidador: la primera vez instala lo necesario, despues abre el navegador.
cd /d "%~dp0"
title Liquidador de sueldos

where py >nul 2>&1 && (set PY=py) || (set PY=python)
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo No encuentro Python. Instalalo desde https://www.python.org/downloads/
  echo y tilda "Add Python to PATH" durante la instalacion.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparando el entorno por primera vez...
  %PY% -m venv .venv || goto error
)

rem Instala o actualiza dependencias solo si cambio requirements.txt.
fc /b requirements.txt .venv\requirements.txt >nul 2>&1
if errorlevel 1 (
  echo Instalando dependencias, puede tardar un par de minutos...
  .venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto error
  copy /y requirements.txt .venv\requirements.txt >nul
)

echo.
echo Liquidador andando en http://127.0.0.1:5000
echo Deja esta ventana abierta mientras lo usas. Para cerrarlo, cerra la ventana.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:5000"
.venv\Scripts\python.exe -m flask --app backend.app:create_app run
pause
exit /b 0

:error
echo.
echo Algo fallo. Copia lo que dice arriba y pasaselo a Claude.
pause
exit /b 1
