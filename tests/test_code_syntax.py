from pathlib import Path


def test_track_analysis_compiles() -> None:
    target = Path("OverallAltitude/Code/Track_analysis_05.py")
    source = target.read_text(encoding="utf-8")
    compile(source, str(target), "exec")
