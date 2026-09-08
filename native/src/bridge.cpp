#include "bridge.h"

#include "Spinnaker.h"
#include "SpinGenApi/SpinnakerGenApi.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>

using namespace Spinnaker;
using namespace Spinnaker::GenApi;

namespace
{
thread_local std::string last_error;

struct SystemContext
{
    SystemPtr system;
    CameraList cameras;
};

struct CameraContext
{
    CameraPtr camera;
    bool acquiring = false;
};

template <size_t N>
void copy_text(char (&destination)[N], const std::string& source)
{
    const size_t count = std::min(source.size(), N - 1);
    std::memcpy(destination, source.data(), count);
    destination[count] = '\0';
}

void copy_text(char* destination, size_t capacity, const std::string& source)
{
    if (destination == nullptr || capacity == 0)
    {
        throw std::invalid_argument("output string buffer is empty");
    }
    const size_t count = std::min(source.size(), capacity - 1);
    std::memcpy(destination, source.data(), count);
    destination[count] = '\0';
}

template <typename Function>
int protect(Function&& function)
{
    try
    {
        last_error.clear();
        function();
        return 0;
    }
    catch (const Spinnaker::Exception& error)
    {
        last_error = error.what();
        return error.GetError() == SPINNAKER_ERR_TIMEOUT ? 1 : -1;
    }
    catch (const std::exception& error)
    {
        last_error = error.what();
        return -1;
    }
    catch (...)
    {
        last_error = "unknown native exception";
        return -1;
    }
}

SystemContext& system_context(void* opaque)
{
    if (opaque == nullptr)
    {
        throw std::invalid_argument("system handle is null");
    }
    return *static_cast<SystemContext*>(opaque);
}

CameraContext& camera_context(void* opaque)
{
    if (opaque == nullptr)
    {
        throw std::invalid_argument("camera handle is null");
    }
    return *static_cast<CameraContext*>(opaque);
}

INodeMap& node_map(CameraContext& context, const std::string& qualified_name, std::string& node_name)
{
    constexpr const char* stream_prefix = "TLStream:";
    if (qualified_name.rfind(stream_prefix, 0) == 0)
    {
        node_name = qualified_name.substr(std::strlen(stream_prefix));
        return context.camera->GetTLStreamNodeMap();
    }
    node_name = qualified_name;
    return context.camera->GetNodeMap();
}

INodeMap& scoped_map(CameraContext& context, int scope)
{
    if (scope == 0)
    {
        return context.camera->GetNodeMap();
    }
    if (scope == 1)
    {
        return context.camera->GetTLStreamNodeMap();
    }
    throw std::invalid_argument("nodemap scope must be 0 (device) or 1 (TLStream)");
}

const char* access_name(EAccessMode access)
{
    switch (access)
    {
    case NI: return "NI";
    case NA: return "NA";
    case WO: return "WO";
    case RO: return "RO";
    case RW: return "RW";
    default: return "UNKNOWN";
    }
}

const char* interface_name(EInterfaceType type)
{
    switch (type)
    {
    case intfIValue: return "IValue";
    case intfIBase: return "IBase";
    case intfIInteger: return "IInteger";
    case intfIBoolean: return "IBoolean";
    case intfICommand: return "ICommand";
    case intfIFloat: return "IFloat";
    case intfIString: return "IString";
    case intfIRegister: return "IRegister";
    case intfICategory: return "ICategory";
    case intfIEnumeration: return "IEnumeration";
    case intfIEnumEntry: return "IEnumEntry";
    case intfIPort: return "IPort";
    default: return "IUnknown";
    }
}

void read_node_value(INode* node, bool ignore_cache, RSValue& out)
{
    std::memset(&out, 0, sizeof(out));
    if (node == nullptr || !IsReadable(node))
    {
        throw std::runtime_error("node is not readable");
    }

    switch (node->GetPrincipalInterfaceType())
    {
    case intfIInteger:
        out.type = RS_VALUE_INTEGER;
        out.integer = CIntegerPtr(node)->GetValue(false, ignore_cache);
        return;
    case intfIFloat:
        out.type = RS_VALUE_FLOAT;
        out.real = CFloatPtr(node)->GetValue(false, ignore_cache);
        return;
    case intfIBoolean:
        out.type = RS_VALUE_BOOLEAN;
        out.integer = CBooleanPtr(node)->GetValue(false, ignore_cache) ? 1 : 0;
        return;
    case intfIEnumeration:
    {
        out.type = RS_VALUE_STRING;
        CEnumerationPtr enumeration(node);
        IEnumEntry* entry = enumeration->GetCurrentEntry(false, ignore_cache);
        if (entry == nullptr)
        {
            throw std::runtime_error("enumeration has no current entry");
        }
        copy_text(out.text, std::string(entry->GetSymbolic().c_str()));
        return;
    }
    case intfIString:
        out.type = RS_VALUE_STRING;
        copy_text(out.text, std::string(CStringPtr(node)->GetValue(false, ignore_cache).c_str()));
        return;
    default:
    {
        CValuePtr value(node);
        if (!value.IsValid())
        {
            throw std::runtime_error("node does not expose a readable value interface");
        }
        out.type = RS_VALUE_STRING;
        copy_text(out.text, std::string(value->ToString(false, ignore_cache).c_str()));
    }
    }
}

std::string node_value_string(INode* node)
{
    RSValue value{};
    read_node_value(node, false, value);
    switch (value.type)
    {
    case RS_VALUE_INTEGER: return std::to_string(value.integer);
    case RS_VALUE_FLOAT: return std::to_string(value.real);
    case RS_VALUE_BOOLEAN: return value.integer ? "true" : "false";
    case RS_VALUE_STRING: return value.text;
    default: return {};
    }
}

void populate_numeric_metadata(INode* node, RSNodeInfo& info)
{
    if (node->GetPrincipalInterfaceType() == intfIInteger)
    {
        CIntegerPtr value(node);
        info.minimum = static_cast<double>(value->GetMin());
        info.maximum = static_cast<double>(value->GetMax());
        info.increment = static_cast<double>(value->GetInc());
        info.flags |= RS_NODE_HAS_MIN | RS_NODE_HAS_MAX | RS_NODE_HAS_INC;
    }
    else if (node->GetPrincipalInterfaceType() == intfIFloat)
    {
        CFloatPtr value(node);
        info.minimum = value->GetMin();
        info.maximum = value->GetMax();
        info.flags |= RS_NODE_HAS_MIN | RS_NODE_HAS_MAX;
        try
        {
            info.increment = value->GetInc();
            if (std::isfinite(info.increment) && info.increment > 0.0)
            {
                info.flags |= RS_NODE_HAS_INC;
            }
        }
        catch (...)
        {
        }
    }
}

double exposure_time(CameraContext& context, const ImagePtr& image)
{
    try
    {
        return static_cast<double>(image->GetChunkData().GetExposureTime());
    }
    catch (...)
    {
        CFloatPtr exposure(context.camera->GetNodeMap().GetNode("ExposureTime"));
        if (!IsReadable(exposure))
        {
            return std::numeric_limits<double>::quiet_NaN();
        }
        return exposure->GetValue(false, true);
    }
}

void release_image(ImagePtr& image)
{
    if (image)
    {
        image->Release();
        image = nullptr;
    }
}
} // namespace

const char* rs_last_error()
{
    return last_error.c_str();
}

int rs_system_create(void** out_system)
{
    return protect([&]() {
        if (out_system == nullptr)
        {
            throw std::invalid_argument("out_system is null");
        }
        std::unique_ptr<SystemContext> context(new SystemContext());
        context->system = System::GetInstance();
        context->cameras = context->system->GetCameras();
        *out_system = context.release();
    });
}

int rs_system_destroy(void* opaque)
{
    return protect([&]() {
        std::unique_ptr<SystemContext> context(static_cast<SystemContext*>(opaque));
        if (!context)
        {
            return;
        }
        context->cameras.Clear();
        if (context->system)
        {
            context->system->ReleaseInstance();
            context->system = nullptr;
        }
    });
}

int rs_camera_count(void* opaque, uint32_t* out_count)
{
    return protect([&]() {
        if (out_count == nullptr)
        {
            throw std::invalid_argument("out_count is null");
        }
        *out_count = static_cast<uint32_t>(system_context(opaque).cameras.GetSize());
    });
}

int rs_camera_serial(void* opaque, uint32_t index, char* out, size_t capacity)
{
    return protect([&]() {
        SystemContext& context = system_context(opaque);
        if (index >= context.cameras.GetSize())
        {
            throw std::out_of_range("camera index is out of range");
        }
        CameraPtr camera = context.cameras.GetByIndex(index);
        CStringPtr serial(camera->GetTLDeviceNodeMap().GetNode("DeviceSerialNumber"));
        if (!IsReadable(serial))
        {
            throw std::runtime_error("DeviceSerialNumber is not readable");
        }
        copy_text(out, capacity, std::string(serial->GetValue().c_str()));
    });
}

int rs_camera_open(void* opaque, const char* serial, void** out_camera)
{
    return protect([&]() {
        if (serial == nullptr || out_camera == nullptr)
        {
            throw std::invalid_argument("serial or out_camera is null");
        }
        SystemContext& system = system_context(opaque);
        CameraPtr camera = system.cameras.GetBySerial(serial);
        if (!camera)
        {
            throw std::runtime_error(std::string("camera not found: ") + serial);
        }
        camera->Init();
        try
        {
            std::unique_ptr<CameraContext> context(new CameraContext());
            context->camera = camera;
            *out_camera = context.release();
        }
        catch (...)
        {
            camera->DeInit();
            throw;
        }
    });
}

int rs_camera_close(void* opaque)
{
    return protect([&]() {
        std::unique_ptr<CameraContext> context(static_cast<CameraContext*>(opaque));
        if (!context)
        {
            return;
        }
        if (context->acquiring || context->camera->IsStreaming())
        {
            context->camera->EndAcquisition();
        }
        context->camera->DeInit();
        context->camera = nullptr;
    });
}

int rs_node_read(void* opaque, const char* qualified_name, int ignore_cache, RSValue* out_value)
{
    return protect([&]() {
        if (qualified_name == nullptr || out_value == nullptr)
        {
            throw std::invalid_argument("node name or output value is null");
        }
        CameraContext& context = camera_context(opaque);
        std::string node_name;
        INode* node = node_map(context, qualified_name, node_name).GetNode(node_name.c_str());
        if (node == nullptr)
        {
            throw std::runtime_error(std::string("node not found: ") + qualified_name);
        }
        read_node_value(node, ignore_cache != 0, *out_value);
    });
}

int rs_node_write(void* opaque, const char* qualified_name, const char* text)
{
    return protect([&]() {
        if (qualified_name == nullptr || text == nullptr)
        {
            throw std::invalid_argument("node name or value is null");
        }
        CameraContext& context = camera_context(opaque);
        std::string node_name;
        INode* node = node_map(context, qualified_name, node_name).GetNode(node_name.c_str());
        if (node == nullptr || !IsWritable(node))
        {
            throw std::runtime_error(std::string("node is not writable: ") + qualified_name);
        }
        CValuePtr value(node);
        if (!value.IsValid())
        {
            throw std::runtime_error(std::string("node has no writable value interface: ") + qualified_name);
        }
        value->FromString(text, true);
    });
}

int rs_node_count(void* opaque, int scope, uint32_t* out_count)
{
    return protect([&]() {
        if (out_count == nullptr)
        {
            throw std::invalid_argument("out_count is null");
        }
        NodeList_t nodes;
        scoped_map(camera_context(opaque), scope).GetNodes(nodes);
        *out_count = static_cast<uint32_t>(nodes.size());
    });
}

int rs_node_info(void* opaque, int scope, uint32_t index, RSNodeInfo* out_info)
{
    return protect([&]() {
        if (out_info == nullptr)
        {
            throw std::invalid_argument("out_info is null");
        }
        std::memset(out_info, 0, sizeof(*out_info));
        NodeList_t nodes;
        scoped_map(camera_context(opaque), scope).GetNodes(nodes);
        if (index >= nodes.size())
        {
            throw std::out_of_range("node index is out of range");
        }
        INode* node = nodes[index];
        if (node == nullptr)
        {
            throw std::runtime_error("nodemap contains a null node");
        }
        copy_text(out_info->name, std::string(node->GetName().c_str()));
        copy_text(out_info->access, access_name(node->GetAccessMode()));
        copy_text(out_info->type, interface_name(node->GetPrincipalInterfaceType()));
        try
        {
            populate_numeric_metadata(node, *out_info);
        }
        catch (...)
        {
        }
        if (IsReadable(node))
        {
            try
            {
                copy_text(out_info->value, node_value_string(node));
                out_info->flags |= RS_NODE_HAS_VALUE;
            }
            catch (const std::exception& error)
            {
                copy_text(out_info->value, std::string("ERROR: ") + error.what());
            }
            catch (...)
            {
                copy_text(out_info->value, "ERROR: value read failed");
            }
        }
    });
}

int rs_begin_acquisition(void* opaque)
{
    return protect([&]() {
        CameraContext& context = camera_context(opaque);
        if (context.acquiring)
        {
            throw std::runtime_error("acquisition is already active");
        }
        context.camera->BeginAcquisition();
        context.acquiring = true;
    });
}

int rs_grab_into(
    void* opaque,
    void* destination,
    size_t destination_bytes,
    uint32_t timeout_ms,
    RSFrameInfo* out_info)
{
    return protect([&]() {
        CameraContext& context = camera_context(opaque);
        if (!context.acquiring)
        {
            throw std::runtime_error("acquisition is not active");
        }
        if (destination == nullptr || out_info == nullptr || timeout_ms == 0)
        {
            throw std::invalid_argument("destination, frame info, and timeout must be valid");
        }

        ImagePtr image;
        try
        {
            image = context.camera->GetNextImage(timeout_ms);
            const ImageStatus status = image->GetImageStatus();
            if (image->IsIncomplete())
            {
                throw std::runtime_error(
                    "incomplete image, status=" + std::to_string(static_cast<int>(status)));
            }

            const size_t width = image->GetWidth();
            const size_t height = image->GetHeight();
            const size_t required = width * height;
            const size_t stride = image->GetStride();
            if (destination_bytes < required)
            {
                throw std::runtime_error(
                    "destination buffer is too small: need " + std::to_string(required));
            }
            if (stride < width)
            {
                throw std::runtime_error("image stride is smaller than image width");
            }

            const auto* source = static_cast<const uint8_t*>(image->GetData());
            auto* target = static_cast<uint8_t*>(destination);
            if (stride == width)
            {
                std::memcpy(target, source, required);
            }
            else
            {
                for (size_t row = 0; row < height; ++row)
                {
                    std::memcpy(target + row * width, source + row * stride, width);
                }
            }

            std::memset(out_info, 0, sizeof(*out_info));
            out_info->frame_id = image->GetFrameID();
            out_info->timestamp_ns = image->GetTimeStamp();
            out_info->exposure_us = exposure_time(context, image);
            out_info->width = static_cast<uint32_t>(width);
            out_info->height = static_cast<uint32_t>(height);
            out_info->image_bytes = static_cast<uint64_t>(required);
            out_info->image_status = static_cast<int32_t>(status);
            release_image(image);
        }
        catch (...)
        {
            release_image(image);
            throw;
        }
    });
}

int rs_discard_one(void* opaque, uint32_t timeout_ms)
{
    return protect([&]() {
        CameraContext& context = camera_context(opaque);
        if (!context.acquiring)
        {
            throw std::runtime_error("acquisition is not active");
        }
        ImagePtr image;
        try
        {
            image = context.camera->GetNextImage(timeout_ms);
            release_image(image);
        }
        catch (...)
        {
            release_image(image);
            throw;
        }
    });
}

int rs_end_acquisition(void* opaque)
{
    return protect([&]() {
        CameraContext& context = camera_context(opaque);
        if (context.acquiring || context.camera->IsStreaming())
        {
            context.camera->EndAcquisition();
        }
        context.acquiring = false;
    });
}

