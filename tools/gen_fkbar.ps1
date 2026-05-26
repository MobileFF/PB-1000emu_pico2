# gen_fkbar.ps1
# Extract the LCKEY..CALC key row from references/face.bmp,
# scale to 320x42, convert to RGB565 big-endian binary -> mp/fkbar.raw

Add-Type -AssemblyName System.Drawing

$repo   = Split-Path -Parent $PSScriptRoot
$src    = Join-Path $repo "references\face.bmp"
$dst    = Join-Path $repo "mp\fkbar.raw"

# Crop coordinates in the original 617x580 BMP
$crop_x = 62; $crop_y = 234; $crop_w = 304; $crop_h = 40
$out_w  = 320; $out_h  = 42

$bmp = [System.Drawing.Bitmap]::new($src)

$scaled = [System.Drawing.Bitmap]::new($out_w, $out_h)
$g = [System.Drawing.Graphics]::FromImage($scaled)
$g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.DrawImage($bmp,
    [System.Drawing.Rectangle]::new(0, 0, $out_w, $out_h),
    [System.Drawing.Rectangle]::new($crop_x, $crop_y, $crop_w, $crop_h),
    [System.Drawing.GraphicsUnit]::Pixel)
$g.Dispose()
$bmp.Dispose()

$bytes = [System.Collections.Generic.List[byte]]::new()
for ($y = 0; $y -lt $out_h; $y++) {
    for ($x = 0; $x -lt $out_w; $x++) {
        $px  = $scaled.GetPixel($x, $y)
        $r5  = [int]($px.R -shr 3)
        $g6  = [int]($px.G -shr 2)
        $b5  = [int]($px.B -shr 3)
        $rgb = ($r5 -shl 11) -bor ($g6 -shl 5) -bor $b5
        $bytes.Add([byte](($rgb -shr 8) -band 0xFF))
        $bytes.Add([byte]($rgb -band 0xFF))
    }
}
$scaled.Dispose()

[System.IO.File]::WriteAllBytes($dst, $bytes.ToArray())
Write-Output "Written: $dst  ($($bytes.Count) bytes = ${out_w}x${out_h} RGB565)"
