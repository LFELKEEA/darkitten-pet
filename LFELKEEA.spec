# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# 安装包只带示例插件；QQ 桥接、专注守护等从插件库单独安装，避免升级时覆盖用户版本。
_plugin_root = os.path.join(SPECPATH, 'plugins')
_example_plugins = sorted(
    name for name in os.listdir(_plugin_root) if name.startswith('example.')
)
datas = [('assets', 'assets'), ('docs', 'docs'), ('version.json', '.')]
# Live2D 运行库随包分发，插件运行时从 exe 同级目录加载。
if os.path.isdir(os.path.join(SPECPATH, 'vendor')):
    datas.append(('vendor', 'vendor'))
for _name in _example_plugins:
    datas.append((os.path.join('plugins', _name), os.path.join('plugins', _name)))
datas += collect_data_files('_sounddevice_data')
binaries = collect_dynamic_libs('sherpa_onnx')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'sounddevice',
        '_sounddevice',
        '_sounddevice_data',
        '_cffi_backend',
        'cffi',
        'sherpa_onnx',
        'sherpa_onnx.lib._sherpa_onnx',
        # 插件是运行时动态导入的，分析器看不到它们用到的标准库。
        'sqlite3',
        '_sqlite3',
        'xml',
        'xml.etree',
        'xml.etree.ElementTree',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LFELKEEA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LFELKEEA',
)