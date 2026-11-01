# 打包单文件 exe（双击启动面板）
# 用法：pwsh -File scripts/build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$py = Join-Path $root "tools\venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "找不到 venv：$py" }
if (-not (Test-Path "$root\frontend\dist\index.html")) {
    throw "缺少前端产物：先 cd frontend && pnpm install && pnpm build"
}

# --noconsole：双击不弹黑框；日志写 logs\app.log
& $py -m PyInstaller --noconfirm --clean --onefile --noconsole --name WechatAutoOrganize `
    --add-data "$root\frontend\dist;frontend\dist" `
    --add-data "$root\vendor\wcdb-key-tool\wcdb_key_tool_windows.py;vendor\wcdb-key-tool" `
    --add-data "$root\vendor\wechat-decrypt\decode_image.py;vendor\wechat-decrypt" `
    --add-data "$root\vendor\wechat-decrypt\find_image_key.py;vendor\wechat-decrypt" `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    --hidden-import Crypto.Cipher.AES `
    --hidden-import Crypto.Util.Padding `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\scripts\serve.py"

if (Test-Path "$root\dist\WechatAutoOrganize.exe") {
    $mb = [math]::Round((Get-Item "$root\dist\WechatAutoOrganize.exe").Length / 1MB, 1)
    Write-Host "打包完成：dist\WechatAutoOrganize.exe ($mb MB)" -ForegroundColor Green
} else {
    throw "打包失败"
}
