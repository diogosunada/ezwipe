# PyInstaller spec. Build on Windows:  pyinstaller packaging/ezwipe.spec
# uac_admin=True makes Windows ask for administrator rights on start,
# which raw disk access needs.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis([os.path.join(ROOT, "packaging", "launch_gui.py")], pathex=[os.path.join(ROOT, "src")])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="EZWipe", console=False, uac_admin=True)

d = Analysis([os.path.join(ROOT, "packaging", "launch_diag.py")], pathex=[os.path.join(ROOT, "src")])
dpyz = PYZ(d.pure)
dexe = EXE(dpyz, d.scripts, d.binaries, d.datas, name="EZWipe-Diagnostic", console=True, uac_admin=True)
