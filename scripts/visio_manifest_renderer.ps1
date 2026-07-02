param(
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$VsdxPath,
    [Parameter(Mandatory=$true)][string]$RenderReportPath,
    [ValidateSet('replace','append','new-page')][string]$PageMode = 'replace',
    [int]$PageIndex = 1,
    [double]$PageW = 0,
    [double]$PageH = 0,
    [switch]$Visible
)

$ErrorActionPreference = 'Stop'
$script:LayerOrder = @('background','assets','panels','sections','icons','connectors','texts','annotations')
$script:ShapeMap = @{}
$script:ElementMap = @{}
$script:PanelMap = @{}
$script:LayerMap = @{}
$script:Reports = New-Object System.Collections.Generic.List[object]

function Q([string]$value) { '"' + ($value -replace '"','""') + '"' }
function F([double]$value) { $value.ToString('0.########', [Globalization.CultureInfo]::InvariantCulture) }

function Hex-To-Rgb([string]$color, [string]$fallback = 'RGB(17,17,17)') {
    if (-not $color) { return $fallback }
    if ($color -match '^#([0-9A-Fa-f]{6})$') {
        return "RGB($([Convert]::ToInt32($matches[1].Substring(0,2),16)),$([Convert]::ToInt32($matches[1].Substring(2,2),16)),$([Convert]::ToInt32($matches[1].Substring(4,2),16)))"
    }
    if ($color -match '^#([0-9A-Fa-f]{3})$') {
        $r=[Convert]::ToInt32(($matches[1][0].ToString()*2),16); $g=[Convert]::ToInt32(($matches[1][1].ToString()*2),16); $b=[Convert]::ToInt32(($matches[1][2].ToString()*2),16)
        return "RGB($r,$g,$b)"
    }
    if ($color -match '^RGB\(') { return $color }
    return $fallback
}

function Set-Cell($shape, [string]$cell, [string]$formula, [switch]$Required) {
    try { $shape.CellsU($cell).FormulaU = $formula }
    catch { if ($Required) { throw "Failed to set $cell on $($shape.NameU): $($_.Exception.Message)" } }
}

function Sanitize-Name([string]$value) {
    $safe = [regex]::Replace($value, '[^A-Za-z0-9_]+', '_').Trim('_')
    if (-not $safe) { $safe = 'element' }
    if ($safe.Length -gt 80) { $safe = $safe.Substring(0,80) }
    return "FE_$safe"
}

function Add-ShapeData($shape, [string]$row, [string]$label, [string]$value) {
    $rowSafe = [regex]::Replace($row, '[^A-Za-z0-9_]+', '_')
    try { $null = $shape.CellsU("Prop.$rowSafe.Value") }
    catch { $null = $shape.AddNamedRow(243, $rowSafe, 0) }
    Set-Cell $shape "Prop.$rowSafe.Label" (Q $label) -Required
    Set-Cell $shape "Prop.$rowSafe.Value" (Q $value) -Required
}

function Set-Metadata($shape, $element, [string]$kind) {
    $shape.NameU = Sanitize-Name ([string]$element.id)
    Add-ShapeData $shape 'ManifestId' 'Manifest ID' ([string]$element.id)
    Add-ShapeData $shape 'ElementType' 'Element type' ([string]$element.type)
    Add-ShapeData $shape 'Decision' 'Decision' ([string]$element.decision)
    Add-ShapeData $shape 'RenderKind' 'Render kind' $kind
    if ($null -ne $element.confidence) { Add-ShapeData $shape 'Confidence' 'Confidence' ([string]$element.confidence) }
    if ($element.latex) { Add-ShapeData $shape 'Latex' 'LaTeX' ([string]$element.latex) }
    if ($element.source_bbox) { Add-ShapeData $shape 'SourceBBox' 'Source bounding box' ($element.source_bbox | ConvertTo-Json -Compress) }
    return ($shape.CellsU('Prop.ManifestId.Value').ResultStr('') -eq [string]$element.id)
}

function Get-LayerName($element) {
    if ($element.layer -and $script:LayerOrder -contains [string]$element.layer) { return [string]$element.layer }
    switch ([string]$element.type) {
        'image' { return 'assets' }
        'text' { return 'texts' }
        'math' { return 'texts' }
        'formula' { return 'texts' }
        'connector' { return 'connectors' }
        'line' { return 'connectors' }
        'path' { return 'connectors' }
        'polyline' { return 'connectors' }
        'circle' { return 'icons' }
        'ellipse' { return 'icons' }
        'polygon' { return 'icons' }
        default { if ([string]$element.class -match 'panel') { return 'panels' } else { return 'sections' } }
    }
}

function Add-To-Layer($shape, [string]$name) {
    if (-not $script:LayerMap.ContainsKey($name)) {
        try { $layer = $script:Page.Layers.ItemU($name) }
        catch { $layer = $script:Page.Layers.Add($name) }
        $script:LayerMap[$name] = $layer
    }
    $script:LayerMap[$name].Add($shape, 1)
}

function Map-X([double]$x) { $script:PageWidth * $x / $script:CanvasWidth }
function Map-Y([double]$y) { $script:PageHeight - ($script:PageHeight * $y / $script:CanvasHeight) }

function Resolve-PanelValue($element, [string]$name, [double]$value) {
    if ([string]$element.coordinate_space -ne 'panel') { return $value }
    $panel = $script:PanelMap[[string]$element.panel_id]
    if ($null -eq $panel) { throw "Panel not found for $($element.id): $($element.panel_id)" }
    switch ($name) {
        'x' { return [double]$panel.x + [double]$panel.w * $value }
        'y' { return [double]$panel.y + [double]$panel.h * $value }
        'w' { return [double]$panel.w * $value }
        'h' { return [double]$panel.h * $value }
    }
    return $value
}

function Resolve-Box($element) {
    $x = Resolve-PanelValue $element 'x' ([double]$element.x)
    $y = Resolve-PanelValue $element 'y' ([double]$element.y)
    $w = Resolve-PanelValue $element 'w' ([double]$element.w)
    $h = Resolve-PanelValue $element 'h' ([double]$element.h)
    [pscustomobject]@{ x=$x; y=$y; w=$w; h=$h }
}

function Resolve-Point($element, [double]$x, [double]$y) {
    [pscustomobject]@{ x=(Resolve-PanelValue $element 'x' $x); y=(Resolve-PanelValue $element 'y' $y) }
}

function Draw-RectFromBox($box) {
    $script:Page.DrawRectangle((Map-X $box.x), (Map-Y ($box.y+$box.h)), (Map-X ($box.x+$box.w)), (Map-Y $box.y))
}

function Style-Shape($shape, $element, [switch]$TextOnly) {
    $fill = [string]$element.fill
    $stroke = [string]$element.stroke
    if ($TextOnly -or $fill -eq 'none' -or -not $fill) { Set-Cell $shape 'FillPattern' '0' }
    else { Set-Cell $shape 'FillPattern' '1'; Set-Cell $shape 'FillForegnd' (Hex-To-Rgb $fill 'RGB(255,255,255)') }
    if ($TextOnly -or $stroke -eq 'none' -or -not $stroke) { Set-Cell $shape 'LinePattern' '0' }
    else {
        Set-Cell $shape 'LinePattern' $(if ($element.dasharray) {'2'} else {'1'})
        Set-Cell $shape 'LineColor' (Hex-To-Rgb $stroke)
        if ($null -ne $element.stroke_width) { Set-Cell $shape 'LineWeight' ((F ([double]$element.stroke_width * 0.75)) + ' pt') }
    }
    if ($null -ne $element.opacity) {
        $trans = [math]::Max(0, [math]::Min(100, (1-[double]$element.opacity)*100))
        Set-Cell $shape 'FillTransp' ((F $trans) + '%'); Set-Cell $shape 'LineTransp' ((F $trans) + '%')
    }
    if ($element.rx) { Set-Cell $shape 'Rounding' ((F (Map-X ([double]$element.rx))) + ' in') }
}

function Style-Line($shape, $element) {
    Set-Cell $shape 'FillPattern' '0'
    Set-Cell $shape 'LinePattern' $(if ($element.dasharray) {'2'} else {'1'})
    Set-Cell $shape 'LineColor' (Hex-To-Rgb ([string]$element.stroke))
    Set-Cell $shape 'LineWeight' ((F ([double]$(if ($null -ne $element.stroke_width) {$element.stroke_width} else {1}) * 0.75)) + ' pt')
    if ($element.arrow_end) { Set-Cell $shape 'EndArrow' '4' }
    if ($element.arrow_start) { Set-Cell $shape 'BeginArrow' '4' }
}

function Style-Text($shape, $element) {
    $font = if ($element.font_family) { [string]$element.font_family } else { $script:DefaultFont }
    $font = ($font -split ',')[0].Trim(' ','"')
    Set-Cell $shape 'Char.Font' ("FONT(" + (Q $font) + ")")
    $size = if ($element.font_size) { [double]$element.font_size } else { 16.0 }
    $fontPt = $size * $script:PageWidth * 72.0 / $script:CanvasWidth
    Set-Cell $shape 'Char.Size' ((F $fontPt) + ' pt')
    Set-Cell $shape 'Char.Color' (Hex-To-Rgb ([string]$element.fill))
    $style = 0
    if ([string]$element.font_weight -match 'bold|[6-9]00') { $style += 1 }
    if ($element.italic) { $style += 2 }
    Set-Cell $shape 'Char.Style' ([string]$style)
    $align = switch ([string]$element.text_anchor) { 'start' {0}; 'end' {2}; default {1} }
    Set-Cell $shape 'Para.HorzAlign' ([string]$align)
    Set-Cell $shape 'VerticalAlign' '1'
    foreach ($margin in 'TxtMarginLeft','TxtMarginRight','TxtMarginTop','TxtMarginBottom') { Set-Cell $shape $margin '1 pt' }
}

function Get-TextBox($element) {
    $size = if ($element.font_size) { [double]$element.font_size } else { 16.0 }
    $value = if ($element.lines) { (@($element.lines) -join "`n") } else { [string]$element.text }
    if ($null -ne $element.w -and $null -ne $element.h) { return Resolve-Box $element }
    $textLines = @($value -split "`n")
    $maxChars = ($textLines | ForEach-Object { $_.Length } | Measure-Object -Maximum).Maximum
    $w = [math]::Max($size*2, $maxChars*$size*0.82 + 8)
    $h = [math]::Max($size*1.4, $textLines.Count*$size*1.35)
    $x = Resolve-PanelValue $element 'x' ([double]$element.x)
    $y = Resolve-PanelValue $element 'y' ([double]$element.y)
    switch ([string]$element.text_anchor) { 'middle' {$x -= $w/2}; 'end' {$x -= $w} }
    $y -= $h*0.78
    [pscustomobject]@{x=$x;y=$y;w=$w;h=$h}
}

function Group-Shapes([object[]]$shapes) {
    $items = @($shapes | Where-Object { $null -ne $_ })
    if ($items.Count -eq 0) { return $null }
    if ($items.Count -eq 1) { return $items[0] }
    $selection = $script:Page.CreateSelection(0, 0, $null)
    foreach ($shape in $items) { $selection.Select($shape, 2) }
    return $selection.Group()
}

function Parse-Points([string]$points, $element, [switch]$Close) {
    $numbers = [regex]::Matches($points, '[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?') | ForEach-Object { [double]$_.Value }
    if ($numbers.Count -lt 4 -or $numbers.Count % 2 -ne 0) { throw "Invalid points for $($element.id)" }
    $mapped = New-Object System.Collections.Generic.List[double]
    for ($i=0; $i -lt $numbers.Count; $i+=2) {
        $point = Resolve-Point $element $numbers[$i] $numbers[$i+1]
        $mapped.Add((Map-X $point.x)); $mapped.Add((Map-Y $point.y))
    }
    if ($Close) { $mapped.Add($mapped[0]); $mapped.Add($mapped[1]) }
    return ,([double[]]$mapped.ToArray())
}

function Draw-Path($element) {
    $parts = New-Object System.Collections.Generic.List[object]
    foreach ($segment in @($element.visio_segments)) {
        $p = @($segment.points)
        if ($segment.type -eq 'line') {
            $a=Resolve-Point $element ([double]$p[0]) ([double]$p[1]); $b=Resolve-Point $element ([double]$p[2]) ([double]$p[3])
            $shape=$script:Page.DrawLine((Map-X $a.x),(Map-Y $a.y),(Map-X $b.x),(Map-Y $b.y)); Style-Line $shape $element; $parts.Add($shape)
        } elseif ($segment.type -eq 'bezier') {
            $values=New-Object System.Collections.Generic.List[double]
            for($i=0;$i -lt 8;$i+=2){$pt=Resolve-Point $element ([double]$p[$i]) ([double]$p[$i+1]);$values.Add((Map-X $pt.x));$values.Add((Map-Y $pt.y))}
            [System.Array]$bezierPoints = [double[]]$values.ToArray()
            $shape=$script:Page.DrawBezier([ref]$bezierPoints,3,8); Style-Line $shape $element; $parts.Add($shape)
        }
    }
    if ($parts.Count -eq 0) { throw "Path $($element.id) has no drawable segments" }
    return Group-Shapes $parts.ToArray()
}

function Draw-Element($element) {
    $type = [string]$element.type
    $kind = "visio-$type"
    $shape = $null
    switch ($type) {
        'rect' { $shape=Draw-RectFromBox (Resolve-Box $element); Style-Shape $shape $element }
        'text' {
            $shape=Draw-RectFromBox (Get-TextBox $element); Style-Shape $shape $element -TextOnly
            $shape.Text = if ($element.lines) { @($element.lines) -join "`n" } else { [string]$element.text }; Style-Text $shape $element
        }
        'circle' {
            $cx=Resolve-PanelValue $element 'x' ([double]$element.x);$cy=Resolve-PanelValue $element 'y' ([double]$element.y);$r=Resolve-PanelValue $element 'w' ([double]$element.r)
            $box=[pscustomobject]@{x=$cx-$r;y=$cy-$r;w=2*$r;h=2*$r};$shape=$script:Page.DrawOval((Map-X $box.x),(Map-Y ($box.y+$box.h)),(Map-X ($box.x+$box.w)),(Map-Y $box.y));Style-Shape $shape $element
        }
        'ellipse' {
            $cx=Resolve-PanelValue $element 'x' ([double]$element.x);$cy=Resolve-PanelValue $element 'y' ([double]$element.y);$rx=Resolve-PanelValue $element 'w' ([double]$element.rx);$ry=Resolve-PanelValue $element 'h' ([double]$element.ry)
            $shape=$script:Page.DrawOval((Map-X ($cx-$rx)),(Map-Y ($cy+$ry)),(Map-X ($cx+$rx)),(Map-Y ($cy-$ry)));Style-Shape $shape $element
        }
        'line' {
            $a=Resolve-Point $element ([double]$element.x1) ([double]$element.y1);$b=Resolve-Point $element ([double]$element.x2) ([double]$element.y2)
            $shape=$script:Page.DrawLine((Map-X $a.x),(Map-Y $a.y),(Map-X $b.x),(Map-Y $b.y));Style-Line $shape $element
        }
        'polyline' { [System.Array]$pts=Parse-Points ([string]$element.points) $element;$shape=$script:Page.DrawPolyline([ref]$pts,8);Style-Line $shape $element }
        'polygon' { [System.Array]$pts=Parse-Points ([string]$element.points) $element -Close;$shape=$script:Page.DrawPolyline([ref]$pts,0);Style-Shape $shape $element }
        'path' { $shape=Draw-Path $element;$kind='visio-path-group' }
        'image' {
            $file=[string]$element.resolved_file;if(-not $file){$file=[string]$element.href};if(-not (Test-Path -LiteralPath $file)){throw "Image file not found: $file"}
            $box=Resolve-Box $element;$shape=$script:Page.Import([IO.Path]::GetFullPath($file));Set-Cell $shape 'PinX' ((F (Map-X ($box.x+$box.w/2)))+' in');Set-Cell $shape 'PinY' ((F (Map-Y ($box.y+$box.h/2)))+' in');Set-Cell $shape 'Width' ((F (Map-X $box.w))+' in');Set-Cell $shape 'Height' ((F ($script:PageHeight*$box.h/$script:CanvasHeight))+' in');$kind='raster-asset'
        }
        {$_ -in @('math','formula')} {
            $box=Resolve-Box $element
            try {
                $shape=$script:Page.Import([string]$element.formula_file);Set-Cell $shape 'PinX' ((F (Map-X ($box.x+$box.w/2)))+' in');Set-Cell $shape 'PinY' ((F (Map-Y ($box.y+$box.h/2)))+' in');Set-Cell $shape 'Width' ((F (Map-X $box.w))+' in');Set-Cell $shape 'Height' ((F ($script:PageHeight*$box.h/$script:CanvasHeight))+' in');$kind=if([int]$shape.Type -eq 2){'visio-group'}else{'imported-vector'}
            } catch {
                $shape=Draw-RectFromBox $box;Style-Shape $shape $element -TextOnly;$shape.Text=[string]$element.latex;Style-Text $shape $element;$kind='text-fallback'
            }
        }
        'connector' {
            $from=$script:ShapeMap[[string]$element.from_id];$to=$script:ShapeMap[[string]$element.to_id]
            if($null -eq $from -or $null -eq $to){throw "Connector endpoints are not rendered: $($element.from_id) -> $($element.to_id)"}
            $shape=$script:Page.DrawLine($from.CellsU('PinX').ResultIU,$from.CellsU('PinY').ResultIU,$to.CellsU('PinX').ResultIU,$to.CellsU('PinY').ResultIU)
            $shape.CellsU('BeginX').GlueToPos($from,0.5,0.5);$shape.CellsU('EndX').GlueToPos($to,0.5,0.5);Style-Line $shape $element;$kind='dynamic-connector'
        }
        default { throw "Unsupported type: $type" }
    }
    $metadata = Set-Metadata $shape $element $kind
    Add-To-Layer $shape (Get-LayerName $element)
    return [pscustomobject]@{shape=$shape;kind=$kind;metadata=$metadata}
}

function Add-Report($element,[string]$status,[string]$kind,[bool]$metadata,[string]$message='') {
    $script:Reports.Add([pscustomobject]@{id=[string]$element.id;type=[string]$element.type;status=$status;render_kind=$kind;metadata_ok=$metadata;message=$message})
}

$manifest = Get-Content -Raw -LiteralPath $ManifestPath -Encoding UTF8 | ConvertFrom-Json
foreach($panel in @($manifest.panels)){$script:PanelMap[[string]$panel.id]=$panel}
foreach($element in @($manifest.elements)){$script:ElementMap[[string]$element.id]=$element}
$script:CanvasWidth=[double]$manifest.canvas.width;$script:CanvasHeight=[double]$manifest.canvas.height
$script:DefaultFont=if($manifest.visio.default_font){[string]$manifest.visio.default_font}else{'Arial'}
if($PageW -le 0){$PageW=if($manifest.visio.page_width_in){[double]$manifest.visio.page_width_in}else{16.0}}
if($PageH -le 0){$PageH=if($manifest.visio.page_height_in){[double]$manifest.visio.page_height_in}else{$PageW*$script:CanvasHeight/$script:CanvasWidth}}
$script:PageWidth=$PageW;$script:PageHeight=$PageH

$visio=$null;$doc=$null;$saved=$false
try {
    $visio=New-Object -ComObject Visio.Application;$visio.Visible=[bool]$Visible;$visio.AlertResponse=7;[void]($visio.EventsEnabled=0)
    if(Test-Path -LiteralPath $VsdxPath){$doc=$visio.Documents.Open([IO.Path]::GetFullPath($VsdxPath))}else{$doc=$visio.Documents.Add('')}
    if($PageMode -eq 'new-page' -and $doc.Pages.Count -ge 1){$script:Page=$doc.Pages.Add()}else{while($doc.Pages.Count -lt $PageIndex){$null=$doc.Pages.Add()};$script:Page=$doc.Pages.Item($PageIndex)}
    $script:Page.PageSheet.CellsU('PageWidth').FormulaU=(F $PageW)+' in';$script:Page.PageSheet.CellsU('PageHeight').FormulaU=(F $PageH)+' in'
    if($PageMode -eq 'replace'){while($script:Page.Shapes.Count -gt 0){$script:Page.Shapes.Item(1).Delete()}}
    foreach($layerName in $script:LayerOrder){try{$layer=$script:Page.Layers.ItemU($layerName)}catch{$layer=$script:Page.Layers.Add($layerName)};$script:LayerMap[$layerName]=$layer}
    $rank=@{};for($i=0;$i -lt $script:LayerOrder.Count;$i++){$rank[$script:LayerOrder[$i]]=$i}
    $ordered=@($manifest.elements)|Sort-Object @{Expression={$rank[(Get-LayerName $_)]}},@{Expression={if($null -ne $_.z_index){[double]$_.z_index}else{0}}}
    $deferred=@($ordered|Where-Object{$_.type -eq 'connector'});$direct=@($ordered|Where-Object{$_.type -ne 'connector'})
    foreach($element in $direct){try{$result=Draw-Element $element;$script:ShapeMap[[string]$element.id]=$result.shape;Add-Report $element 'ok' $result.kind $result.metadata}catch{Add-Report $element 'failed' '' $false $_.Exception.Message;throw}}
    foreach($element in $deferred){try{$result=Draw-Element $element;$script:ShapeMap[[string]$element.id]=$result.shape;Add-Report $element 'ok' $result.kind $result.metadata}catch{Add-Report $element 'failed' '' $false $_.Exception.Message;throw}}
    $groups=@{}
    foreach($element in @($manifest.elements)){
        if($element.type -eq 'connector'){continue}
        $gid=if($element.group_id){'group:'+[string]$element.group_id}elseif($element.panel_id){'panel:'+[string]$element.panel_id}else{$null}
        if($gid){if(-not $groups.ContainsKey($gid)){$groups[$gid]=New-Object System.Collections.Generic.List[object]};$groups[$gid].Add($script:ShapeMap[[string]$element.id])}
    }
    foreach($gid in @($groups.Keys|Sort-Object)){
        if($groups[$gid].Count -gt 1){$group=Group-Shapes $groups[$gid].ToArray();$group.NameU=Sanitize-Name $gid;Add-ShapeData $group 'GroupId' 'Semantic group' $gid;Add-To-Layer $group 'panels'}
    }
    if(Test-Path -LiteralPath $VsdxPath){$null=$doc.Save()}else{$null=$doc.SaveAs([IO.Path]::GetFullPath($VsdxPath))}
    $saved=$true
    $verification=@()
    foreach($element in @($manifest.elements)){
        $shape=$script:ShapeMap[[string]$element.id]
        $ok=$false
        try{$ok=($shape.CellsU('Prop.ManifestId.Value').ResultStr('') -eq [string]$element.id)}catch{}
        $verification+=[pscustomobject]@{id=[string]$element.id;metadata_ok=$ok;shape_name=if($shape){$shape.NameU}else{$null}}
    }
    foreach($entry in $script:Reports){$match=$verification|Where-Object{$_.id -eq $entry.id}|Select-Object -First 1;if($match){$entry.metadata_ok=[bool]$match.metadata_ok}}
    [pscustomobject]@{status='ok';page_index=$script:Page.Index;page_width_in=$PageW;page_height_in=$PageH;shape_count=$script:Page.Shapes.Count;elements=$script:Reports.ToArray()}|ConvertTo-Json -Depth 12|Set-Content -LiteralPath $RenderReportPath -Encoding UTF8
} catch {
    [pscustomobject]@{status='failed';message=$_.Exception.Message;stack=$_.ScriptStackTrace;elements=$script:Reports.ToArray()}|ConvertTo-Json -Depth 12|Set-Content -LiteralPath $RenderReportPath -Encoding UTF8
    Write-Error ($_.Exception.Message + "`n" + $_.ScriptStackTrace)
    throw
} finally {
    if($doc){try{if($saved){$doc.Saved=$true}else{$doc.Saved=$true};$doc.Close()}catch{}}
    if($visio){try{[void]($visio.EventsEnabled=-1);$visio.Quit()}catch{}}
}
