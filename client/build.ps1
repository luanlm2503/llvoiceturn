# Đóng gói app thành exe bằng Nuitka. Chạy từ thư mục client/ sau khi đã cài: pip install -e ".[build]"
# Ví dụ: .\build.ps1 -ApiBaseUrl https://api.llvoice.vn
param(
    [Parameter(Mandatory = $true)]
    [string]$ApiBaseUrl
)

$buildFile = "llvoice/_build.py"
"API_BASE_URL = `"$ApiBaseUrl`"" | Out-File -Encoding utf8 $buildFile

try {
    python -m nuitka `
        --standalone `
        --onefile `
        --enable-plugin=pyside6 `
        --include-data-dir=llvoice/locales=llvoice/locales `
        --include-data-dir=llvoice/assets=llvoice/assets `
        --windows-icon-from-ico=llvoice/assets/icon.ico `
        --windows-console-mode=disable `
        --company-name="LLVoiceTool" `
        --product-name="LLVoiceTool" `
        --file-version=0.1.0 `
        --product-version=0.1.0 `
        --output-dir=dist `
        --output-filename=LLVoiceTool.exe `
        llvoice/main.py
}
finally {
    Remove-Item $buildFile -ErrorAction SilentlyContinue
}
