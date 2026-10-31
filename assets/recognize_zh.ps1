param(
    [Parameter(Mandatory = $true)][string]$AudioPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$Culture = "zh-CN"
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

try {
    Add-Type -AssemblyName System.Speech
    $cultureInfo = [System.Globalization.CultureInfo]::GetCultureInfo($Culture)
    $engine = [System.Speech.Recognition.SpeechRecognitionEngine]::new($cultureInfo)
    $engine.LoadGrammar([System.Speech.Recognition.DictationGrammar]::new())
    $engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds(3)
    $engine.EndSilenceTimeout = [TimeSpan]::FromSeconds(0.6)
    $engine.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromSeconds(0.9)
    $engine.BabbleTimeout = [TimeSpan]::FromSeconds(2)
    $engine.SetInputToWaveFile($AudioPath)

    # 只识别一段：文件读完后 SAPI 再次调用 Recognize() 可能抛异常或挂起，
    # 这里不循环，保证识别进程一定能结束。
    $result = $engine.Recognize()
    $engine.Dispose()

    $text = if ($null -eq $result) { "" } else { $result.Text.Trim() }
    [System.IO.File]::WriteAllText($OutputPath, $text, $utf8)
    if ([string]::IsNullOrWhiteSpace($text)) { exit 3 }
    exit 0
}
catch {
    [System.IO.File]::WriteAllText($OutputPath, $_.Exception.Message, $utf8)
    exit 2
}
