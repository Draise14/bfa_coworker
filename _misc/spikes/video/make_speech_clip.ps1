# Video 0 spike (#374): a ~2-minute speech test clip from Windows' built-in
# System.Speech synthesizer, with deliberate pauses (for silence detection and VAD).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File make_speech_clip.ps1 <out_dir>
# Writes speech_16k_mono.wav (16 kHz / 16-bit / mono, whisper's native input) and
# speech_44k_stereo.wav (44.1 kHz / 16-bit / stereo, a typical editing format), and per file
# <file>.words.tsv: the synthesizer's own word-start times (ground truth for timestamp error).
param([string]$OutDir)
Add-Type -AssemblyName System.Speech
$sentences = @(
  "Hello, and welcome to this short test of automatic captions in the video sequencer.",
  "Today we are going to cut a talking head clip into a vertical short.",
  "First, the editor removes the long pauses between sentences, so the pace feels tighter.",
  "Then every spoken word gets its own start and end time.",
  "Those times become caption strips that pop on screen exactly when the word is said.",
  "Numbers are tricky, so here are a few: twenty twenty six, three point five, and one hundred percent.",
  "Names are tricky too. Blender, Bforartists and whisper should all come out right.",
  "Next, the shot is reframed for a nine by sixteen screen, with a blurred copy behind it.",
  "A title enters with a little pop, stays for a moment, and slides away.",
  "If the speaker pauses for a long time, like this,",
  "the silence finder should notice the gap and offer to cut it.",
  "Short pauses, however, should stay, because they make speech sound natural.",
  "Finally, the captions can be exported as a subtitle file and played in any video player.",
  "That is the whole workflow, from a raw clip to a captioned vertical short.",
  "Thank you for listening, and see you in the next video."
)
$pauses = @(1.2, 0.6, 2.5, 0.4, 1.0, 3.0, 0.8, 1.5, 0.5, 4.0, 0.7, 2.0, 1.0, 0.6, 1.5)
function Build {
  $pb = New-Object System.Speech.Synthesis.PromptBuilder
  $pb.AppendBreak([TimeSpan]::FromSeconds(1.0))
  for ($i = 0; $i -lt $sentences.Count; $i++) {
    $pb.AppendText($sentences[$i])
    $pb.AppendBreak([TimeSpan]::FromSeconds($pauses[$i]))
  }
  return $pb
}
$fmts = @(
  @{ name = "speech_16k_mono.wav";   rate = 16000; ch = [System.Speech.AudioFormat.AudioChannel]::Mono },
  @{ name = "speech_44k_stereo.wav"; rate = 44100; ch = [System.Speech.AudioFormat.AudioChannel]::Stereo }
)
Add-Type -ReferencedAssemblies System.Speech -TypeDefinition @"
using System.Collections.Generic;
using System.Speech.Synthesis;
public class WordLog {
  public List<string> Rows = new List<string>();
  public void Hook(SpeechSynthesizer s) {
    s.SpeakProgress += (o, e) => Rows.Add(e.AudioPosition.TotalSeconds.ToString("F3",
      System.Globalization.CultureInfo.InvariantCulture) + "	" + e.Text);
  }
}
"@
foreach ($f in $fmts) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.Rate = -2
  $log = New-Object WordLog
  $log.Hook($s)
  $info = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo($f.rate, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, $f.ch)
  $path = Join-Path $OutDir $f.name
  $s.SetOutputToWaveFile($path, $info)
  $s.Speak((Build))
  $voice = $s.Voice.Name
  $s.Dispose()
  # Ground truth for word starts (SpeakProgress: audio position where each word begins).
  $log.Rows | Set-Content -Encoding UTF8 ($path + ".words.tsv")
  Write-Output ("wrote " + $path + " voice=" + $voice + " words=" + $log.Rows.Count)
}
