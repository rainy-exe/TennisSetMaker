@echo off
REM ==========================================================================
REM run_all_simulations.bat
REM
REM Runs simulate.py once for every combination below and saves each result
REM under a name that says what it is, e.g.
REM     comparison_results\13players_3courts_SameGender_HardAvoid_6rounds_ResetEachSession.xlsx
REM then builds comparison_results\comparison_summary.xlsx (one row per run).
REM
REM   Players per day : 13 (3 courts, 1 sits out)  and  26 (6 courts, 2 sit out)
REM   Gender          : Open, Mixed, SameGender
REM   Variety mode    : HardAvoid, Soft, RoundRobin
REM   = 18 runs. Every run uses the same seed, so the same people turn up on the
REM     same days in every 13-player run (and in every 26-player run) -- only the
REM     settings differ, which makes the results directly comparable.
REM
REM Put this file (and compare_runs.py) in simulation_test and double-click it,
REM or run it from a Command Prompt / PowerShell in that folder.
REM The 26-player runs take about 1-2 minutes each; the 13-player runs a few seconds.
REM ==========================================================================

setlocal EnableExtensions
cd /d "%~dp0"

REM ---- settings you might want to change --------------------------------------
set ROUNDS=6
set DAYS=50
REM Who-played-with-who memory: 1 = reset every session, 2 = carry across sessions
set MEMORY=1
set OUTDIR=comparison_results
REM -----------------------------------------------------------------------------

if "%MEMORY%"=="2" (set MEMNAME=CarryAcrossSessions) else (set MEMNAME=ResetEachSession)

if not exist simulate.py (
    echo simulate.py isn't in %CD% -- put this .bat in simulation_test.
    goto :fail
)
findstr /c:"skill_profile" simulate.py >nul || (
    echo simulate.py here is an old version. Run install_prototype4.sh first:
    echo     bash install_prototype4.sh
    goto :fail
)

REM ---- pick Python: WSL first (what you normally use), then Windows Python ----
set "PY="
where wsl >nul 2>nul && (wsl -e python3 -c "import openpyxl" >nul 2>nul && set "PY=wsl -e python3")
if not defined PY (where py >nul 2>nul && (py -3 -c "import openpyxl" >nul 2>nul && set "PY=py -3"))
if not defined PY (where python >nul 2>nul && (python -c "import openpyxl" >nul 2>nul && set "PY=python"))
if not defined PY (
    echo Couldn't find a Python with openpyxl installed.
    echo In WSL run:   python3 -m pip install openpyxl
    goto :fail
)
echo Using: %PY%

if not exist "%OUTDIR%" mkdir "%OUTDIR%"
set "INPUTS=%TEMP%\tsm_sim_inputs.txt"
set /a RUN=0

for %%P in (13 26) do (
    for %%G in (0 1 2) do (
        for %%V in (1 2 3) do (
            call :run %%P %%G %%V
        )
    )
)

echo.
echo All %RUN% runs finished. Building the comparison summary...
if exist compare_runs.py (
    %PY% compare_runs.py "%OUTDIR%"
) else (
    echo compare_runs.py not found -- skipping the summary sheet.
)
echo.
echo Results are in %CD%\%OUTDIR%
pause
exit /b 0


REM ---- one run: %1 = players per day, %2 = gender, %3 = variety mode ----------
:run
set PLAYERS=%1
if "%1"=="13" (set COURTS=3) else (set COURTS=6)
if "%2"=="0" set GNAME=Open
if "%2"=="1" set GNAME=Mixed
if "%2"=="2" set GNAME=SameGender
if "%3"=="1" set VNAME=HardAvoid
if "%3"=="2" set VNAME=Soft
if "%3"=="3" set VNAME=RoundRobin
set NAME=%PLAYERS%players_%COURTS%courts_%GNAME%_%VNAME%_%ROUNDS%rounds_%MEMNAME%
set /a RUN+=1

REM Answers to simulate.py's prompts, in order:
REM rounds, gender, variety mode, memory, courts, players per day, days
>"%INPUTS%"  echo %ROUNDS%
>>"%INPUTS%" echo %2
>>"%INPUTS%" echo %3
>>"%INPUTS%" echo %MEMORY%
>>"%INPUTS%" echo %COURTS%
>>"%INPUTS%" echo %PLAYERS%
>>"%INPUTS%" echo %DAYS%

echo [%RUN%/18] %NAME%
if exist simulation_results_prototype4.xlsx del simulation_results_prototype4.xlsx
%PY% simulate.py < "%INPUTS%" > "%OUTDIR%\%NAME%.log" 2>&1
if exist simulation_results_prototype4.xlsx (
    move /y simulation_results_prototype4.xlsx "%OUTDIR%\%NAME%.xlsx" >nul
) else (
    echo     FAILED -- see %OUTDIR%\%NAME%.log
)
goto :eof


:fail
pause
exit /b 1
