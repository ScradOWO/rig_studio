#pragma once

#include <cstddef>
#include <cstdint>

#ifdef _WIN32
#define RS_EXPORT extern "C" __declspec(dllexport)
#else
#define RS_EXPORT extern "C"
#endif

enum RSValueType : int32_t
{
    RS_VALUE_NONE = 0,
    RS_VALUE_INTEGER = 1,
    RS_VALUE_FLOAT = 2,
    RS_VALUE_BOOLEAN = 3,
    RS_VALUE_STRING = 4,
};

enum RSNodeFlags : uint32_t
{
    RS_NODE_HAS_VALUE = 1,
    RS_NODE_HAS_MIN = 2,
    RS_NODE_HAS_MAX = 4,
    RS_NODE_HAS_INC = 8,
};

struct RSValue
{
    int32_t type;
    int32_t reserved;
    int64_t integer;
    double real;
    char text[512];
};

struct RSNodeInfo
{
    char name[256];
    char access[16];
    char type[32];
    char value[512];
    double minimum;
    double maximum;
    double increment;
    uint32_t flags;
};

struct RSFrameInfo
{
    uint64_t frame_id;
    uint64_t timestamp_ns;
    double exposure_us;
    uint32_t width;
    uint32_t height;
    uint64_t image_bytes;
    int32_t image_status;
    int32_t reserved;
};

RS_EXPORT const char* rs_last_error();
RS_EXPORT int rs_system_create(void** out_system);
RS_EXPORT int rs_system_destroy(void* system);
RS_EXPORT int rs_camera_count(void* system, uint32_t* out_count);
RS_EXPORT int rs_camera_serial(void* system, uint32_t index, char* out, size_t capacity);
RS_EXPORT int rs_camera_open(void* system, const char* serial, void** out_camera);
RS_EXPORT int rs_camera_close(void* camera);
RS_EXPORT int rs_node_read(void* camera, const char* name, int ignore_cache, RSValue* out_value);
RS_EXPORT int rs_node_write(void* camera, const char* name, const char* value);
RS_EXPORT int rs_node_count(void* camera, int scope, uint32_t* out_count);
RS_EXPORT int rs_node_info(void* camera, int scope, uint32_t index, RSNodeInfo* out_info);
RS_EXPORT int rs_begin_acquisition(void* camera);
RS_EXPORT int rs_grab_into(
    void* camera,
    void* destination,
    size_t destination_bytes,
    uint32_t timeout_ms,
    RSFrameInfo* out_info);
RS_EXPORT int rs_discard_one(void* camera, uint32_t timeout_ms);
RS_EXPORT int rs_end_acquisition(void* camera);

