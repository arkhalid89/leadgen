# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for LeadGen Desktop App.
Bundles Flask app, templates, static files, and all scrapers.
"""

import os
import sys

block_cipher = None

# Paths
ROOT = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['desktop.py'],
    pathex=[ROOT],
    binaries=[],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'flask',
        'flask_cors',
        'jinja2',
        'markupsafe',
        'werkzeug',
        'selenium',
        'selenium.webdriver',
        'selenium.webdriver.chrome',
        'selenium.webdriver.chrome.service',
        'selenium.webdriver.chrome.options',
        'selenium.webdriver.common.by',
        'selenium.webdriver.common.keys',
        'selenium.webdriver.support.ui',
        'selenium.webdriver.support.expected_conditions',
        'webdriver_manager',
        'webdriver_manager.chrome',
        'bs4',
        'lxml',
        'lxml.html',
        'pandas',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'webview',
        'scraper',
        'linkedin_scraper',
        'instagram_scraper',
        'web_crawler',
        'engineio',
        'engineio.async_drivers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LeadGen',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,            # No terminal window
    icon=None,                # Set to 'icon.ico' on Windows or 'icon.icns' on macOS if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LeadGen',
)

# macOS .app bundle
app = BUNDLE(
    coll,
    name='LeadGen.app',
    icon=None,                # Set to 'icon.icns' if you have one
    bundle_identifier='com.leadgen.suite',
    info_plist={
        'CFBundleName': 'LeadGen',
        'CFBundleDisplayName': 'LeadGen Suite',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
    },
)
