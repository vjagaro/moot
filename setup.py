from setuptools import setup

setup(
    name="moot",
    version="0.1.0",
    py_modules=["moot"],
    install_requires=[],
    entry_points="""
        [console_scripts]
        moot=moot:main
    """,
)
