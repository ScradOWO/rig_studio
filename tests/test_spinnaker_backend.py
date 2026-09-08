from types import SimpleNamespace

import numpy as np
import pytest

from rig_studio.backend.base import FrameGapError
from rig_studio.backend.spinnaker_backend import SpinnakerBackend


class FakeNativeApi:
    def __init__(self) -> None:
        self.present = ["100", "200", "outside"]
        self.nodes = {
            "Width": 8,
            "Height": 4,
            "TLStream:StreamBufferCountMode": "Auto",
            "TLStream:StreamBufferCountManual": 4,
            "TLStream:StreamBufferHandlingMode": "NewestOnly",
            "TLStream:StreamOutputBufferCount": 0,
        }
        self.next_frame_id = 1
        self.closed = []
        self.destroyed = False

    def system_create(self):
        return "system"

    def system_destroy(self, system) -> None:
        assert system == "system"
        self.destroyed = True

    def enumerate(self, system):
        assert system == "system"
        return self.present

    def camera_open(self, system, serial):
        assert system == "system"
        return serial

    def camera_close(self, camera) -> None:
        self.closed.append(camera)

    def node_read(self, camera, name, ignore_cache):
        return self.nodes[name]

    def node_write(self, camera, name, value) -> None:
        self.nodes[name] = value

    def nodemap_dump(self, camera):
        return {"Width": {"access": "RW", "type": "IInteger", "value": 8}}

    def begin(self, camera) -> None:
        pass

    def end(self, camera) -> None:
        pass

    def grab_into(self, camera, destination, timeout_s):
        destination.reshape(-1)[:] = self.next_frame_id
        info = SimpleNamespace(
            frame_id=self.next_frame_id,
            timestamp_ns=self.next_frame_id * 1000,
            exposure_us=5000.0,
            width=8,
            height=4,
            image_bytes=32,
        )
        self.next_frame_id += 1
        return info

    def discard_one(self, camera, timeout_s) -> None:
        pass


def make_backend(api: FakeNativeApi) -> SpinnakerBackend:
    return SpinnakerBackend(["100", "200"], api_factory=lambda _: api)


def test_native_backend_filters_serials_and_configures_stream():
    api = FakeNativeApi()
    backend = make_backend(api)
    assert backend.enumerate() == ["100", "200"]

    handle = backend.open("100")
    handle.begin_acquisition(16)
    assert api.nodes["TLStream:StreamBufferCountMode"] == "Manual"
    assert api.nodes["TLStream:StreamBufferCountManual"] == 16
    assert api.nodes["TLStream:StreamBufferHandlingMode"] == "OldestFirst"

    destination = np.empty((4, 8), np.uint8)
    frame = handle.grab_into(destination, 0.1)
    assert np.shares_memory(frame.image, destination)
    assert np.all(destination == 1)
    assert frame.frame_id == 1
    assert frame.timestamp_ns == 1000

    backend.close()
    assert api.closed == ["100"]
    assert api.destroyed


def test_native_backend_detects_gaps_across_single_frame_calls():
    api = FakeNativeApi()
    backend = make_backend(api)
    handle = backend.open("100")
    handle.begin_acquisition(4)
    destination = np.empty((4, 8), np.uint8)
    handle.grab_into(destination, 0.1)
    api.next_frame_id = 3
    with pytest.raises(FrameGapError, match="1 -> 3"):
        handle.grab_into(destination, 0.1)
    backend.close()


def test_native_backend_rejects_noncontiguous_destination():
    api = FakeNativeApi()
    backend = make_backend(api)
    handle = backend.open("100")
    handle.begin_acquisition(4)
    with pytest.raises(ValueError, match="C-contiguous"):
        handle.grab_into(np.empty((4, 16), np.uint8)[:, ::2], 0.1)
    backend.close()

