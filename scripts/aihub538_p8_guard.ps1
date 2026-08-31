$script:Aihub538P8ReservedFileKeys = [System.Collections.Generic.HashSet[string]]::new(
  [string[]] @(
    '55080', '55091', '55102', '55106', '55107', '55108', '55109', '55110',
    '55111', '55081', '55082', '55083', '55084', '55085', '55086', '55087',
    '55088', '55089', '55090', '55092', '55093', '55094', '55095', '55096',
    '55097', '55098', '55099', '55100', '55101', '55103', '55104', '55105',
    '55112', '55123', '55134', '55138', '55139', '55140', '55141', '55142',
    '55143', '55113', '55114', '55115', '55116', '55117', '55118', '55119',
    '55120', '55121', '55122', '55124', '55125', '55126', '55127', '55128',
    '55129', '55130', '55131', '55132', '55133', '55135', '55136', '55137'
  )
)

function Assert-NoAihub538P8GenericFileKey([string]$FileKeys) {
  if ($FileKeys -cnotmatch '^[1-9][0-9]*(,[1-9][0-9]*)*$') {
    throw 'AI Hub file keys must use canonical positive-decimal spelling without aliases or leading zeroes.'
  }
  foreach ($fileKey in $FileKeys.Split(',')) {
    if ($script:Aihub538P8ReservedFileKeys.Contains($fileKey)) {
      throw 'AI Hub 538 P8 validation file keys require the typed one-shot entrypoint, regardless of the supplied dataset key.'
    }
  }
}
