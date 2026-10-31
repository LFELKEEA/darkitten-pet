param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$Language = "zh-Hans"
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        })[0]
    if ($null -eq $asTaskGeneric) { throw "WinRT AsTask not available" }

    # 这里刻意展开写，不用 PowerShell 函数包一层。某些机器上函数参数传入
    # WinRT COM 对象后会丢失接口投影，导致 Wait 抛 AggregateException。
    $op = [Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)
    $file = $asTaskGeneric.MakeGenericMethod([Windows.Storage.StorageFile]).Invoke($null, @($op)).Result

    $op = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    $stream = $asTaskGeneric.MakeGenericMethod([Windows.Storage.Streams.IRandomAccessStream]).Invoke($null, @($op)).Result

    $op = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = $asTaskGeneric.MakeGenericMethod([Windows.Graphics.Imaging.BitmapDecoder]).Invoke($null, @($op)).Result

    $op = $decoder.GetSoftwareBitmapAsync()
    $bitmap = $asTaskGeneric.MakeGenericMethod([Windows.Graphics.Imaging.SoftwareBitmap]).Invoke($null, @($op)).Result

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        try {
            $language = [Windows.Globalization.Language]::new($Language)
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        }
        catch {
            $engine = $null
        }
    }
    if ($null -eq $engine) { throw "OCR engine unavailable. Install a language OCR pack first." }

    $op = $engine.RecognizeAsync($bitmap)
    $result = $asTaskGeneric.MakeGenericMethod([Windows.Media.Ocr.OcrResult]).Invoke($null, @($op)).Result

    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $result.Lines) { $lines.Add($line.Text) }
    $text = ($lines -join "`n").Trim()

    [System.IO.File]::WriteAllText($OutputPath, $text, $utf8)
    if ([string]::IsNullOrWhiteSpace($text)) { exit 3 }
    exit 0
}
catch {
    [System.IO.File]::WriteAllText($OutputPath, $_.Exception.Message, $utf8)
    exit 2
}
