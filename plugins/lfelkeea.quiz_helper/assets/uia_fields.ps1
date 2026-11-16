param(
    [Parameter(Mandatory = $true)][long]$Hwnd,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-Rect($el) {
    $r = $el.Current.BoundingRectangle
    $x = [double]$r.X; $y = [double]$r.Y
    $w = [double]$r.Width; $h = [double]$r.Height
    if ([double]::IsNaN($x) -or [double]::IsInfinity($x)) { return $null }
    if ([double]::IsNaN($y) -or [double]::IsInfinity($y)) { return $null }
    if ($w -le 0 -or $h -le 0) { return $null }
    return [pscustomobject]@{ x = $x; y = $y; w = $w; h = $h }
}

function Get-Labels($all) {
    $labels = New-Object System.Collections.Generic.List[object]
    foreach ($el in $all) {
        try {
            $name = $el.Current.Name
            if ([string]::IsNullOrWhiteSpace($name)) { continue }
            $ct = $el.Current.ControlType.ProgrammaticName
            if ($ct -eq "ControlType.Edit" -or $ct -eq "ControlType.Document" -or
                $ct -eq "ControlType.Pane" -or $ct -eq "ControlType.Window") { continue }
            $r = Get-Rect $el
            if ($null -eq $r -or $r.w -lt 4 -or $r.h -lt 4 -or $r.w -gt 1600) { continue }
            $labels.Add([pscustomobject]@{
                name = (($name -replace '\s+', ' ').Trim())
                x = $r.x
                y = $r.y
                w = $r.w
                h = $r.h
                right = $r.x + $r.w
                area = $r.w * $r.h
            })
        } catch { }
    }
    return ,$labels
}

function Set-FieldLabels($fields, $labels) {
    $matched = 0
    foreach ($field in $fields) {
        $cy = $field.y + $field.h / 2
        $best = $null
        $bestGap = [double]::MaxValue
        $bestArea = [double]::MaxValue
        foreach ($lab in $labels) {
            if ($lab.right -gt ($field.x + 10)) { continue }
            if ($cy -lt ($lab.y - 8) -or $cy -gt ($lab.y + $lab.h + 8)) { continue }
            $gap = $field.x - $lab.right
            if ($gap -lt $bestGap -or ($gap -eq $bestGap -and $lab.area -lt $bestArea)) {
                $best = $lab
                $bestGap = $gap
                $bestArea = $lab.area
            }
        }
        $field.label = ""
        if ($null -ne $best) {
            $text = $best.name
            if ($text.Length -gt 120) { $text = $text.Substring(0, 120) }
            $field.label = $text
            $matched++
        }
    }
    return $matched
}

try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes

    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
    if ($null -eq $root) { throw "window not found" }

    $all = $root.FindAll(
        [System.Windows.Automation.TreeScope]::Descendants,
        [System.Windows.Automation.Condition]::TrueCondition
    )

    # 网页正文区域，用来排除地址栏、搜索框这类浏览器自己的输入框。
    $doc = $null
    foreach ($el in $all) {
        if ($el.Current.ControlType.ProgrammaticName -eq "ControlType.Document" -and
            $el.Current.AutomationId -eq "RootWebArea") {
            $doc = Get-Rect $el
            if ($null -ne $doc) { break }
        }
    }

    $fields = New-Object System.Collections.Generic.List[object]
    foreach ($el in $all) {
        try {
            if ($el.Current.ControlType.ProgrammaticName -ne "ControlType.Edit") { continue }
            if (-not $el.Current.IsEnabled -or -not $el.Current.IsKeyboardFocusable) { continue }
            if ($el.Current.IsPassword) { continue }
            $r = Get-Rect $el
            if ($null -eq $r -or $r.w -lt 6 -or $r.h -lt 6) { continue }
            if ($null -ne $doc) {
                if ($r.x -lt ($doc.x - 6) -or $r.y -lt ($doc.y - 6)) { continue }
                if (($r.x + $r.w) -gt ($doc.x + $doc.w + 6)) { continue }
                if (($r.y + $r.h) -gt ($doc.y + $doc.h + 6)) { continue }
            }
            $value = ""
            $vp = $null
            if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
                try { $value = [string]$vp.Current.Value } catch { $value = "" }
            }
            $fields.Add([pscustomobject]@{
                x = [math]::Round($r.x, 1)
                y = [math]::Round($r.y, 1)
                w = [math]::Round($r.w, 1)
                h = [math]::Round($r.h, 1)
                value = ($value -replace '\s+', ' ').Trim()
                label = ""
            })
        } catch { }
    }

    $labels = Get-Labels $all
    $matched = Set-FieldLabels $fields $labels

    # Chromium 首次被 UIA 查询时控件名可能还没建好，等一会儿再取一次标签。
    if ($fields.Count -gt 0 -and $matched -eq 0) {
        Start-Sleep -Milliseconds 450
        $all = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            [System.Windows.Automation.Condition]::TrueCondition
        )
        $labels = Get-Labels $all
        $matched = Set-FieldLabels $fields $labels
    }

    $index = 0
    foreach ($field in $fields) {
        $index++
        $field | Add-Member -NotePropertyName index -NotePropertyValue $index
    }

    $sorted = @($fields | Sort-Object y, x)
    $result = [pscustomobject]@{
        hwnd   = $Hwnd
        title  = $root.Current.Name
        count  = $sorted.Count
        fields = $sorted
    }
    [System.IO.File]::WriteAllText($OutputPath, ($result | ConvertTo-Json -Depth 6), $utf8)
    exit 0
}
catch {
    [System.IO.File]::WriteAllText($OutputPath, $_.Exception.Message, $utf8)
    exit 2
}
