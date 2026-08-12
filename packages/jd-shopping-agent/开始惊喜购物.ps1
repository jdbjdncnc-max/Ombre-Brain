$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath '.\config.local.json')) {
    throw '请先运行“开始设置.ps1”。'
}

$secureKey = Read-Host '请粘贴与 Zeabur 中 OMBRE_JD_WORKER_TOKEN 相同的本地助手密钥（输入不会显示）' -AsSecureString
$keyPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:OMBRE_JD_WORKER_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPtr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPtr)
}

Write-Host ''
Write-Host '先打开京东登录窗口。已经登录过的话，它会很快完成。'
npm run login
if ($LASTEXITCODE -ne 0) {
    throw '京东登录没有完成，本地助手不会启动。'
}

Write-Host ''
Write-Host '你将建立长期授权：额度和每月订单数以 config.local.json 为准。她会自己选择礼物，不会询问喜好或展示候选。'
$authorization = Read-Host '如果同意，请输入：授权'
if ($authorization -ne '授权') {
    throw '没有完成长期额度授权，程序已停止。'
}

npm run authorize
if ($LASTEXITCODE -ne 0) {
    throw '长期额度授权没有建立成功。'
}

Write-Host ''
Write-Host '本地助手开始常驻。保持这个窗口和电脑开启，她就能使用购物工具；按 Ctrl+C 可以随时暂停。'
Write-Host 'Edge 会真实打开京东；如果你不想看到礼物，请不要查看浏览器窗口。'
npm start

Write-Host ''
Write-Host '本地助手已经停止，Ombre 会把购物工具视为离线。'
Read-Host '按 Enter 关闭'
