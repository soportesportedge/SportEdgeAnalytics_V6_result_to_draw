import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const root=process.cwd();
const out=path.join(root,'outputs','v6_result_to_draw_20261011');
const data=JSON.parse(await fs.readFile(path.join(out,'workbook_data.json'),'utf8'));
const summary=JSON.parse(await fs.readFile(path.join(out,'completion_summary.json'),'utf8'));
const wb=Workbook.create();
const percentNames=new Set(['Precision test','Recall test','F1 test','Probabilidad media','Precision backtest','Recall backtest','precision','recall','f1','ap','prevalence','signal_rate','probability_mean','probability_signal_mean','missing','zero']);
const selected={
  'Metricas':['family','partition','rows','positives','tp','fp','fn','precision','recall','f1','ap','brier','probability_mean','probability_signal_mean','threshold','signal_rate'],
  'Backtest temporal':['fold','family','from','to','rows','tp','fp','precision','recall','f1','ap','brier','threshold'],
  'Calibracion':['family','partition','probability_kind','ap','brier','log_loss','ece_10','probability_mean'],
  'EDA':['variable','missing','unique','zero','min','median','max','dictionary_group'],
  'Comparador V4':['name','partition','precision','recall','f1','ap','brier','threshold']
};
const titles={Resumen:'SportEdge V6 result_to_draw',Particiones:'Separación temporal y prevalencia',Metricas:'Resultados por modelo y partición','Backtest temporal':'Cinco folds de evaluación anidada',Calibracion:'Calibración antes y después',Ingenieria:'Fórmulas de las 136 variables de ingeniería','Variables finales':'Variables efectivas por modelo',EDA:'Perfil de los 183 predictores marcados SI',Ciclos:'Correlación y selección en tres ciclos','Comparador V4':'V4 activo evaluado con las etiquetas actuales'};
const views=[];
const labels={family:'Modelo',partition:'Partición',source_rows:'Filas fuente',supervised_rows:'Filas elegibles',supervised_fixtures:'Partidos elegibles',fixtures:'Partidos',prevalence:'Prevalencia',purged:'Filas purgadas',positives:'Positivos',precision:'Precisión',recall:'Recall',f1:'F1',rows:'Snapshots',fold:'Fold',from:'Desde UTC',to:'Hasta UTC',probability_kind:'Probabilidad',probability_mean:'Probabilidad media',probability_signal_mean:'Prob. señales',variable:'Variable',missing:'Nulos',zero:'Ceros',unique:'Valores únicos',formula:'Fórmula',null_policy:'Tratamiento NULL',feature:'Variable',model:'Modelo',candidate_id:'Versión',cycle:'Ciclo',kind:'Tipo',dictionary_group:'Grupo',min:'Mínimo',max:'Máximo',median:'Mediana',candidate_features:'Variables candidatas',new_engineered:'Nuevas ingenierías',versions:'Versiones',versions_passing_validation:'Cumplen en validación',best_validation_ap:'Mejor AP en validación',threshold:'Umbral',signal_rate:'Cobertura',name:'Comparador'};
const integers=new Set(['rows','source_rows','supervised_rows','supervised_fixtures','fixtures','purged','positives','tp','fp','fn','fold','cycle','unique','candidate_features','new_engineered','versions','versions_passing_validation']);
for(const [name,part] of Object.entries(data)){
  const sh=wb.worksheets.add(name);sh.showGridLines=false;
  let cols=selected[name]??part.columns;
  const inds=cols.map(c=>part.columns.indexOf(c));
  const rows=part.rows.map(row=>inds.map(i=>row[i]));
  if(name==='Particiones')rows.sort((a,b)=>['train','calibration','validation','test'].indexOf(a[0])-['train','calibration','validation','test'].indexOf(b[0]));
  if(name==='Resumen')for(const row of rows)row[cols.indexOf('Aprobado')]=row[cols.indexOf('Aprobado')]?'Sí':'No';
  const count=rows.length+5;
  sh.getRangeByIndexes(0,0,count,cols.length).format.font={name:'Arial',size:10,color:'#182b3a'};
  sh.getRange('A2').values=[[titles[name]]];sh.getRange('A2').format.font={name:'Arial',size:15,bold:true,color:'#000000'};
  sh.getRangeByIndexes(4,0,rows.length+1,cols.length).values=[cols.map(c=>labels[c]??c),...rows];
  const head=sh.getRangeByIndexes(4,0,1,cols.length);
  head.format={fill:'#183A59',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,rowHeight:34,horizontalAlignment:'center',verticalAlignment:'center'};
  sh.getRangeByIndexes(5,0,rows.length,cols.length).format.rowHeight=24;
  sh.getRangeByIndexes(5,0,rows.length,cols.length).format.verticalAlignment='center';
  cols.forEach((col,j)=>{
    const range=sh.getRangeByIndexes(4,j,rows.length+1,1);
    range.format.columnWidthPx=['feature','variable'].includes(col)?430:['formula','null_policy','dictionary_group','name'].includes(col)?470:['family','model','Modelo'].includes(col)?240:col==='candidate_id'?285:['from','to'].includes(col)?185:['versions_passing_validation','candidate_features','new_engineered','best_validation_ap'].includes(col)?165:110;
    const body=sh.getRangeByIndexes(5,j,rows.length,1);
    body.format.horizontalAlignment=typeof rows[0]?.[j]==='number'?'right':'left';
    if(percentNames.has(col))body.setNumberFormat('0.0%');
    else if(['Umbral','threshold','brier','log_loss','ece_10','best_validation_ap','rank_score'].includes(col))body.setNumberFormat('0.0000');
    else if(integers.has(col))body.setNumberFormat('#,##0');
    else if(typeof rows[0]?.[j]==='number')body.setNumberFormat('#,##0.00');
  });
  sh.freezePanes.freezeRows(5);
  if(name==='Resumen'){
    sh.getRange('A3').values=[['Seleccionado: '+summary.selected_family+' | Señales accionables deshabilitadas']];
    sh.getRange('A15').values=[['Fuente: CSV etiquetado result_to_draw y auditoría del 11/10/2026. Test sin selección de umbral.']];
    sh.getRange('A17').values=[['Pendientes conservados']];sh.getRange('B17').values=[[summary.unresolved_preserved]];
    sh.getRange('A18').values=[['Snapshots en prueba']];sh.getRange('B18').values=[[summary.test_rows]];
    sh.getRange('A19').values=[['Partidos en prueba']];sh.getRange('B19').values=[[summary.test_fixture_count]];
    sh.getRange('A21').values=[['Requisitos: precisión >50% y recall >25%. Ningún finalista los cumplió en prueba.']];
    sh.getRange('J5').values=[['Cumple test']];
    sh.getRange('J5:J13').format.columnWidthPx=115;
    sh.getRange('J5').format={fill:'#183A59',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,rowHeight:34,horizontalAlignment:'center',verticalAlignment:'center'};
    sh.getRange('I6:J13').format.horizontalAlignment='center';
    for(let k=0;k<rows.length;k++)sh.getRange(`J${k+6}`).formulas=[[`=IF(AND(B${k+6}>0.5,C${k+6}>0.25),"Sí","No")`]];
    sh.getRange('L5:N5').values=[['Modelo','Precisión','Recall']];
    for(let k=0;k<rows.length;k++)sh.getRange(`L${k+6}:N${k+6}`).formulas=[[`=IF(A${k+6}="hist_gradient_boosting","HGB",IF(A${k+6}="gradient_boosting","GB",A${k+6}))`,`=B${k+6}`,`=C${k+6}`]];
    sh.getRange('M6:N13').setNumberFormat('0.0%');
    const chart=sh.charts.add('bar',sh.getRange('L5:N13'));chart.title='Prueba independiente';chart.setPosition('L16','U32');
    chart.titleTextStyle.typeface='Arial';chart.titleTextStyle.fontSize=13;chart.legend={position:'bottom'};
    chart.xAxis={axisType:'textAxis',textStyle:{typeface:'Arial',fontSize:10}};chart.yAxis={numberFormatCode:'0%',numberFormatSourceLinked:false,textStyle:{typeface:'Arial',fontSize:10}};
    views.push({name:'Resumen_grafico',sheet:'Resumen',range:'L15:U33'});
  }
  if(name==='Ingenieria'){
    sh.getRangeByIndexes(5,2,rows.length,2).format.wrapText=true;
    sh.getRangeByIndexes(5,0,rows.length,cols.length).format.rowHeight=42;
  }
  views.push({name,range:name==='Resumen'?'A1:J22':sh.getRangeByIndexes(0,0,Math.min(count,14),Math.min(cols.length,8)).address});
}
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'Resumen!A5:J13',include:'values,formulas',tableMaxRows:10,tableMaxCols:10})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!',options:{useRegex:true,maxResults:20},summary:'Final formula error scan'})).ndjson);
await fs.mkdir(path.join(out,'workbook_previews'),{recursive:true});
for(const v of views){
  const preview=await wb.render({sheetName:v.sheet??v.name,range:v.range,scale:1.5,format:'png'});
  await fs.writeFile(path.join(out,'workbook_previews',v.name.replaceAll(' ','_')+'.png'),new Uint8Array(await preview.arrayBuffer()));
}
const xlsx=await SpreadsheetFile.exportXlsx(wb);await xlsx.save(path.join(out,'Resultados_V6_result_to_draw.xlsx'));
console.log('Exported workbook and rendered all ten sheets');
