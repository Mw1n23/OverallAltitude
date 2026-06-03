from pathlib import Path

from setuptools import find_packages, setup


README = Path(__file__).with_name("README.md").read_text(encoding="utf-8")


setup(
    name="overall-altitude",
    version="0.1.0",
    description="GPX elevation analysis with DEM-backed total ascent estimation.",
    long_description=README,
    long_description_content_type="text/markdown",
    author="Mw1n23",
    license="MIT",
    url="https://github.com/Mw1n23/OverallAltitude",
    python_requires=">=3.10",
    packages=find_packages(include=["OverallAltitude", "OverallAltitude.*"]),
    include_package_data=True,
    package_data={"OverallAltitude": ["RawMaterial/*.gpx"]},
    project_urls={
        "Source": "https://github.com/Mw1n23/OverallAltitude",
        "Issues": "https://github.com/Mw1n23/OverallAltitude/issues",
    },
    extras_require={
        "plot": ["matplotlib>=3.7"],
        "dev": ["pytest>=8"],
    },
    entry_points={
        "console_scripts": [
            "overall-altitude=OverallAltitude.Code.Track_analysis_05:main",
        ]
    },
)
