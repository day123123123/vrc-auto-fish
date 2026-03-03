# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('img', 'img'), ('yolo/runs/fish_detect/weights/best.pt', 'yolo/runs/fish_detect/weights'), ('yolo/dataset/data.yaml', 'yolo/dataset')]
binaries = []
hiddenimports = ['ultralytics', 'cv2', 'keyboard', 'mss', 'PIL', 'psutil', 'win32gui', 'win32con', 'pythonosc']
tmp_ret = collect_all('ultralytics')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_patch.py'],
    excludes=['torch.cuda', 'triton'],
    noarchive=False,
    optimize=0,
)
# Filter out CUDA native DLLs for CPU-only build
_cuda_keywords = ['cublas', 'cublasLt', 'cudnn', 'cufft', 'cusolver',
                  'cusparse', 'torch_cuda', 'nvrtc', 'c10_cuda',
                  'caffe2_nvrtc', 'nvinfer', 'nvToolsExt', 'nccl',
                  'cudart', 'cupti', 'curand', 'nvJitLink', 'nvjpeg',
                  'nvperf']
a.binaries = [b for b in a.binaries
              if not any(k in b[0] for k in _cuda_keywords)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VRC auto fish-CPU',
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
    manifest='admin.manifest',
    uac_admin=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='VRC auto fish-CPU',
)
