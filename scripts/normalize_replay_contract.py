"""Represent unscored structural negatives without inventing a raw model score."""
from pathlib import Path
import csv,io,os
from prepare_research import SOURCE
root=Path(__file__).resolve().parents[1]
out=root/'outputs/v6_result_to_draw_20261011/backtesting_base_completa.csv'
raw=SOURCE.read_bytes().splitlines(keepends=True)
rows=list(csv.reader(out.open(encoding='utf-8-sig',newline='')))
header=rows[0];eligible=header.index('v6_eligible')
indices=[i for i,c in enumerate(header) if c.startswith('v6_') and c.endswith('_raw_score')]
temporary=out.with_suffix('.csv.tmp')
with temporary.open('wb') as f:
    for source,row in zip(raw,rows):
        if row is not header and row[eligible].lower()=='false':
            for i in indices:row[i]=''
        suffix=io.StringIO();csv.writer(suffix,lineterminator='').writerow(row[222:])
        ending=b'\r\n' if source.endswith(b'\r\n') else b'\n' if source.endswith(b'\n') else b''
        body=source[:-len(ending)] if ending else source
        f.write(body+b','+suffix.getvalue().encode('utf8')+ending)
os.replace(temporary,out)
print('Raw score absent for unscored structural negatives; source bytes retained')
