@echo off
REM Build LeadGen desktop app for Windows (.exe)
REM Run from the project root: build_win.bat

echo === LeadGen Windows Build ===
echo.

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Build with PyInstaller (skip BUNDLE step which is macOS-only)
echo Building with PyInstaller...
pyinstaller --noconfirm ^
    --name LeadGen ^
    --windowed ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --hidden-import flask ^
    --hidden-import flask_cors ^
    --hidden-import jinja2 ^
    --hidden-import markupsafe ^
    --hidden-import werkzeug ^
    --hidden-import selenium ^
    --hidden-import selenium.webdriver ^
    --hidden-import selenium.webdriver.chrome ^
    --hidden-import selenium.webdriver.chrome.service ^
    --hidden-import selenium.webdriver.chrome.options ^
    --hidden-import webdriver_manager ^
    --hidden-import webdriver_manager.chrome ^
    --hidden-import bs4 ^
    --hidden-import lxml ^
    --hidden-import lxml.html ^
    --hidden-import pandas ^
    --hidden-import requests ^
    --hidden-import urllib3 ^
    --hidden-import certifi ^
    --hidden-import webview ^
    --hidden-import scraper ^
    --hidden-import linkedin_scraper ^
    --hidden-import instagram_scraper ^
    --hidden-import web_crawler ^
    --exclude-module tkinter ^
    --exclude-module matplotlib ^
    --exclude-module numpy ^
    --exclude-module scipy ^
    desktop.py

echo.
echo === Build Complete ===
echo   EXE: dist\LeadGen\LeadGen.exe
echo.
echo To run: dist\LeadGen\LeadGen.exe
echo Output CSV files saved to: %%USERPROFILE%%\LeadGen_Output\
echo.
echo NOTE: Chrome must be installed for scrapers to work.
