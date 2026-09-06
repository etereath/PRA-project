# Computer Use 截图捕获 SetIsBorderRequired 错误报告

生成日期：2026-06-16

## 1. 问题摘要

在 Windows 10 环境中使用 Codex Computer Use 对桌面窗口执行截图捕获时，`ShadowBot.Shell` 与 `WeChatAppEx` 两个窗口均出现同一错误：

```text
SetIsBorderRequired failed: 不支持此接口 (0x80004002)
```

同样操作在 Windows 11 系统上未复现该问题。当前判断：该问题大概率不是影刀或微信小程序窗口本身的问题，而是 Computer Use 原生 Windows 截图 helper 在 Windows 10 上调用 `Windows.Graphics.Capture.GraphicsCaptureSession.IsBorderRequired` 或其底层接口时，没有做运行时能力检测/兼容降级，导致接口不存在时直接失败。

## 2. 影响范围

受影响能力：

- `get_window_state({ include_screenshot: true })` 无法返回截图。
- 依赖截图坐标的点击、图像验证、视觉定位不可用。
- 由于没有截图，无法安全地通过坐标点击 `WeChatAppEx` 的“商品管理”等入口。

不受影响或部分可用能力：

- `sky.list_apps()` 可枚举应用和窗口。
- `get_window_state({ include_screenshot: false, include_text: true })` 可读取 UI Automation 文本树。
- `WeChatAppEx` 首页文本可读，包括“欢迎使用蚂蚁花团供应商端”“商品管理”“订单管理”“我的收益”等。
- `ShadowBot.Shell` 外层控件文本可读。

## 3. 本机复现环境

问题复现机器：

```text
WindowsProductName: Windows 10 Home
WindowsVersion: 2009
OsBuildNumber: 19045
OsHardwareAbstractionLayer: 10.0.19041.6456
```

Codex / Computer Use 相关版本：

```text
Computer Use 插件版本:
P:\Users\etereath\.codex\plugins\cache\openai-bundled\computer-use\26.609.41114

@oai/sky 运行时:
P:\Users\etereath\AppData\Local\OpenAI\Codex\runtimes\cua_node\789504f803e82e2b\bin\node_modules\@oai\sky

@oai/sky version:
0.4.10

codex-computer-use.exe:
P:\Users\etereath\AppData\Local\OpenAI\Codex\runtimes\cua_node\789504f803e82e2b\bin\node_modules\@oai\sky\bin\windows\codex-computer-use.exe
```

目标窗口：

```text
ShadowBot.Shell
窗口标题: 影刀

WeChatAppEx
窗口标题: 蚂蚁花团供应商
```

## 4. 复现步骤

在 Codex Node REPL / Computer Use 环境中执行等价操作：

```javascript
if (!globalThis.sky) {
  const { setupComputerUseRuntime } = await import(
    "P:/Users/etereath/.codex/plugins/cache/openai-bundled/computer-use/26.609.41114/scripts/computer-use-client.mjs"
  );
  await setupComputerUseRuntime({ globals: globalThis });
}

const apps = await sky.list_apps();
const wechatApp = apps.find(
  (app) =>
    /WeChatAppEx/i.test(app.displayName ?? "") &&
    app.windows?.some((window) => /蚂蚁花团供应商/.test(window.title ?? ""))
);

const wechatWindow = await sky.get_window(
  wechatApp.windows.find((window) => /蚂蚁花团供应商/.test(window.title ?? ""))
);

await sky.get_window_state({
  window: wechatWindow,
  include_screenshot: true,
  include_text: true,
});
```

实际结果：

```text
SetIsBorderRequired failed: 不支持此接口 (0x80004002)
```

预期结果：

```text
返回至少 1 张截图，或在不支持 borderless capture 时继续返回普通截图。
```

## 5. 对照现象

### 5.1 Windows 10

```text
include_screenshot=true
结果: 失败
错误: SetIsBorderRequired failed: 不支持此接口 (0x80004002)
```

```text
include_screenshot=false, include_text=true
结果: 成功
可读取 UI Automation 文本树
```

### 5.2 Windows 11

用户反馈：相同操作在 Windows 11 系统上未遇到截图捕获失败。

建议在 Windows 11 上采集以下信息用于对照：

```powershell
Get-ComputerInfo |
  Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsHardwareAbstractionLayer |
  Format-List
```

```powershell
Get-Content "$env:LOCALAPPDATA\OpenAI\Codex\runtimes\cua_node\<runtime-id>\bin\node_modules\@oai\sky\package.json" -Encoding UTF8
```

```powershell
Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\runtimes\cua_node\<runtime-id>\bin\node_modules\@oai\sky\bin\windows" |
  Select-Object Name,Length,LastWriteTime
```

## 6. 技术判断

Microsoft 文档显示：

- `GraphicsCaptureSession` 基础类在 Windows 10 version 1803 / 10.0.17134.0 引入。
- `GraphicsCaptureSession.IsBorderRequired` 属性要求 `Windows.Foundation.UniversalApiContract v12.0`，文档标注为 Windows 10 version 2104 / 10.0.20348.0 引入。

参考资料：

- https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession
- https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.graphicscapturesession.isborderrequired

本机 Windows 10 build 为 `19045`。虽然它是 Windows 10 22H2，但从实际错误看，Computer Use helper 调用的 `SetIsBorderRequired` 对应接口在该环境中不可用，返回 `0x80004002`。该 HRESULT 通常表示 `E_NOINTERFACE`，即请求的 COM/WinRT 接口不受支持。

因此，推荐上游修复方向是：

1. 在原生 helper 中调用 `SetIsBorderRequired` 前，使用 WinRT API contract / interface 能力检测。
2. 如果接口不可用，不应中断截图流程，应跳过“关闭截图边框”设置，继续普通截图。
3. 如果设置 `IsBorderRequired=false` 失败，也应将它视为非致命错误，最多记录 warning。
4. 截图捕获成功与否不应依赖“是否能隐藏黄色边框”。

伪代码：

```cpp
// 伪代码，仅描述兼容策略
auto session = framePool.CreateCaptureSession(item);

if (SupportsIGraphicsCaptureSession3(session)) {
    try {
        session.IsBorderRequired(false);
    } catch (hresult_no_interface const&) {
        // Windows 10 19045 等环境可能不支持该接口。
        // 忽略，继续截图。
    } catch (...) {
        // 建议记录 warning，但不让截图整体失败。
    }
}

session.StartCapture();
```

## 7. 已做本地热修

### 7.1 `@oai/sky` 子路径导出热修

早期还遇到另一个初始化错误：

```text
Package subpath './dist/project/cua/sky_js/src/targets/windows/internal/computer_use_client_base.js' is not defined by "exports"
```

已在本机缓存文件中添加子路径导出：

```text
P:\Users\etereath\AppData\Local\OpenAI\Codex\runtimes\cua_node\789504f803e82e2b\bin\node_modules\@oai\sky\package.json
```

新增等价配置：

```json
"./dist/project/cua/sky_js/src/targets/windows/internal/computer_use_client_base.js": "./dist/project/cua/sky_js/src/targets/windows/internal/computer_use_client_base.js"
```

该热修仅解决 Computer Use 初始化，不解决截图捕获。

### 7.2 截图失败降级为文本读取

已在本机插件脚本中添加降级逻辑：

```text
P:\Users\etereath\.codex\plugins\cache\openai-bundled\computer-use\26.609.41114\scripts\computer-use-client.mjs
```

逻辑：

```text
当 get_window_state 同时请求 include_screenshot=true 与 include_text=true，
并命中 SetIsBorderRequired failed / 0x80004002 时，
自动重试 include_screenshot=false，只保留文本读取。
```

验证结果：

```text
screenshots: 0
hasText: true
可读到: 商品管理、欢迎使用蚂蚁花团供应商端
```

该热修只能避免整个调用失败，不能让截图真正成功。

## 8. 建议在 Windows 11 上分析的重点

请在 Windows 11 环境中记录以下对照项：

1. Windows 版本和 build：

```powershell
Get-ComputerInfo |
  Select-Object WindowsProductName,WindowsVersion,OsBuildNumber,OsHardwareAbstractionLayer |
  Format-List
```

2. `@oai/sky` 版本：

```powershell
Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\runtimes\cua_node" -Directory |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 3 Name,FullName,LastWriteTime
```

3. `codex-computer-use.exe` 文件版本/时间：

```powershell
Get-ChildItem "$env:LOCALAPPDATA\OpenAI\Codex\runtimes\cua_node" -Recurse -Filter codex-computer-use.exe |
  Select-Object FullName,Length,LastWriteTime
```

4. 截图调用结果：

```javascript
await sky.get_window_state({
  window: targetWindow,
  include_screenshot: true,
  include_text: true,
});
```

5. 如果 Win11 成功，请记录截图返回数量、截图尺寸和窗口类型：

```text
screenshots.length
screenshots[0].width
screenshots[0].height
targetWindow.title
targetWindow.app
```

## 9. 结论

该问题最可能是 Computer Use 原生 Windows 截图 helper 对 `GraphicsCaptureSession.IsBorderRequired` 的兼容处理不足。Windows 11 支持该调用，因此不报错；Windows 10 Home build 19045 上该接口不可用或 QueryInterface 失败，因此返回 `0x80004002`。

建议上游修复为：`SetIsBorderRequired` 调用失败时降级继续截图，而不是让整个截图捕获失败。本地已做“截图失败时降级为文本读取”的临时补丁，但这不是完整修复。
