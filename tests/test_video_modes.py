from rig_studio.camera.profiles import clamp_node_value, scale_geometry_for_video_mode


def test_native_mode_restores_vertically_binned_geometry():
    assert scale_geometry_for_video_mode(
        "Mode2", "Mode0", 768, 750, 128, 100
    ) == (768, 1500, 128, 200)


def test_vertical_binning_only_halves_vertical_geometry():
    assert scale_geometry_for_video_mode(
        "Mode0", "Mode2", 768, 1500, 128, 200
    ) == (768, 750, 128, 100)


def test_two_axis_bin_to_vertical_bin_restores_horizontal_geometry():
    assert scale_geometry_for_video_mode(
        "Mode1", "Mode2", 1024, 750, 0, 120
    ) == (2048, 750, 0, 120)


def test_two_axis_binning_scales_both_axes():
    assert scale_geometry_for_video_mode(
        "Mode1", "Mode0", 1024, 750, 8, 120
    ) == (2048, 1500, 16, 240)


def test_node_value_is_clamped_and_aligned_down():
    entry = {"min": 8, "max": 2048, "inc": 8}
    assert clamp_node_value(entry, 2053) == 2048
    assert clamp_node_value(entry, 1019) == 1016
