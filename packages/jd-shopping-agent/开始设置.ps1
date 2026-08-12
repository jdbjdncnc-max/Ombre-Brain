$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host '正在安装本地浏览器组件……'
npm install --cache .npm-cache

if (-not (Test-Path -LiteralPath '.\config.local.json')) {
    Copy-Item -LiteralPath '.\config.example.json' -Destination '.\config.local.json'
}

Write-Host ''
Write-Host '即将打开设置文件。只需填写 Ombre 地址和长期额度；没有喜好表格，也不需要填写 OpenRouter 模型。'
Start-Process notepad.exe -ArgumentList (Resolve-Path -LiteralPath '.\config.local.json') -Wait

Write-Host ''
Write-Host '设置完成。下一步请双击“开始惊喜购物.ps1”。'
Read-Host '按 Enter 结束'
