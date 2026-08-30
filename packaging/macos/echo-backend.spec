# -*- mode: python ; coding: utf-8 -*-
# macOS mirror of packaging/windows/echo-backend.spec.
# Differences from the Windows spec:
#   - entry script is shared (platform-neutral `from runtime.cli import main`)
#   - UPX is disabled: it is unreliable on Mach-O and would break codesigning
#   - built for the interpreter's native target (Apple Silicon arm64 or Rosetta x86_64)

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


repo_root = Path(SPECPATH).parents[1]
entry = repo_root / "packaging" / "windows" / "echo_backend_entry.py"

remote_plugin_prefixes = (
    "runtime.platform.plugins.bundled.narrative_studio",
    "runtime.platform.plugins.bundled.paper_trading",
)
hiddenimports = [
    module
    for module in collect_submodules("runtime")
    if not module.startswith(remote_plugin_prefixes)
] + [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "multipart",
    "python_multipart",
]

datas = [
    item
    for item in collect_data_files("runtime", include_py_files=False)
    if not any(
        prefix in item[0].replace("\\", "/")
        for prefix in (
            "runtime/platform/plugins/bundled/narrative_studio",
            "runtime/platform/plugins/bundled/paper_trading",
        )
    )
]
reflex_rules = repo_root / "data" / "reflex_rules.yaml"
if reflex_rules.exists():
    datas.append((str(reflex_rules), "data"))

a = Analysis(
    [str(entry)],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tests",
        "frontend",
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "diffusers",
        "accelerate",
        "scipy",
        "sklearn",
        "scikit-learn",
        "pandas",
        "onnxruntime",
        "onnx",
        "tensorboard",
        "tensorflow",
        "keras",
        "matplotlib",
        "seaborn",
        "statsmodels",
        "sympy",
        "nltk",
        "spacy",
        "gensim",
        "xgboost",
        "lightgbm",
        "catboost",
        "cv2",
        "opencv-python",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="echo-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
