def test_import_package():
    import badminton_analysis
    assert hasattr(badminton_analysis, "__version__")
