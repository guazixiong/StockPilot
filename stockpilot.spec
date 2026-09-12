# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 配置：单文件窗口程序
# 构建: python -m PyInstaller stockpilot.spec --noconfirm
import os

a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['PySide6.QtSvg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy',
              # v7.2.10：应用不用 QtNetwork——Qt 的 TLS 后端插件
              # （qopensslbackend，运行时 LoadLibrary libssl-3）与 Python
              # _ssl 共享同一对 libcrypto/libssl-3-x64.dll。剔除整个 Qt
              # Network/TLS 面（含插件），只留 Python 自己的 ssl。
              'PySide6.QtNetwork', 'PySide6.QtNetworkAuth',
              'PySide6.QtWebSockets', 'PySide6.QtHttpServer'],
    noarchive=False,
    optimize=1,
)

# v7.2.10：从二进制清单剔除 Qt 网络栈与 TLS 后端插件（应用零使用）。
# 这同时消灭 Qt 与 Python _ssl 共享 OpenSSL 的并发面，frozen 间歇
# 0xC0000005 的主嫌（详见 v7.2.10 事故记录）。
#
# 注意：**不能**剔 libssl-3-x64.dll/libcrypto-3-x64.dll——conda 环境的
# _ssl.pyd 动态链接它们（import ssl 即报 "SSL module is not available"，
# 18:57 实测全 HTTPS 失效）。剔除范围只到 Qt 自身的网络/TLS DLL。
_EXCLUDE_PATTERNS = (
    'Qt6Network.dll',          # Qt 网络主库（无使用方时被 QtSvg/Gui 传递收集）
    'qopensslbackend.dll',     # Qt OpenSSL TLS 后端（运行时加载 libssl）
    'qschannelbackend.dll',    # Qt Schannel TLS 后端
    'qcertonlybackend.dll',    # Qt 证书后端
)
a.binaries = [b for b in a.binaries
              if not any(p in b[1].replace('\\', '/') for p in _EXCLUDE_PATTERNS)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='StockPilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='stockpilot.ico' if os.path.exists('stockpilot.ico') else None,
)
