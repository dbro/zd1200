'use strict';

const MAGIC=[0x5a,0x44,0x50,0x4d,0x44,0x41,0x59,0x00];
const FORMAT_VERSION=1;

function macAt(bytes,offset){
 const hex=[];for(let i=0;i<6;i++)hex.push(bytes[offset+i].toString(16).padStart(2,'0'));
 return hex.join(':');
}

function parseDay(buffer){
 const bytes=new Uint8Array(buffer),view=new DataView(buffer);
 if(bytes.length<576||!MAGIC.every((value,index)=>bytes[index]===value))throw new Error('Invalid Ping Monitor daily file magic.');
 const version=view.getUint16(8,true),headerSize=view.getUint16(10,true),flags=view.getUint32(12,true);
 const start=view.getUint32(16,true),end=view.getUint32(20,true),roundCount=view.getUint32(24,true),targetCount=view.getUint32(28,true);
 const timeout=view.getUint16(32,true),codeCount=view.getUint16(34,true),timestampsOffset=view.getUint32(36,true);
 const macsOffset=view.getUint32(40,true),samplesOffset=view.getUint32(44,true),generation=view.getUint32(48,true);
 if(version!==FORMAT_VERSION||headerSize!==576||flags!==1||codeCount!==254||end-start!==86400)throw new Error('Unsupported Ping Monitor daily format.');
 if(timestampsOffset!==headerSize||macsOffset!==timestampsOffset+roundCount*4||samplesOffset<macsOffset+targetCount*6||samplesOffset>bytes.length)throw new Error('Invalid Ping Monitor daily offsets.');
 if(targetCount&&roundCount>(bytes.length-samplesOffset)/targetCount)throw new Error('Truncated Ping Monitor daily samples.');
 if(samplesOffset+targetCount*roundCount!==bytes.length)throw new Error('Unexpected Ping Monitor daily file length.');
 const codebook=new Uint16Array(codeCount);let previous=0;
 for(let i=0;i<codeCount;i++){let value=view.getUint16(64+i*2,true);if(value<=previous||value>timeout)throw new Error('Invalid Ping Monitor latency codebook.');codebook[i]=value;previous=value}
 const timestamps=new Uint32Array(roundCount);previous=0;
 for(let i=0;i<roundCount;i++){let value=view.getUint32(timestampsOffset+i*4,true);if((i&&value<=previous)||value<start||value>=end)throw new Error('Invalid Ping Monitor round timestamps.');timestamps[i]=value;previous=value}
 const macs=new Array(targetCount);let previousMac='';
 for(let i=0;i<targetCount;i++){let value=macAt(bytes,macsOffset+i*6);if(previousMac&&value<=previousMac)throw new Error('Invalid Ping Monitor target order.');macs[i]=value;previousMac=value}
 return{bytes,codebook,start,end,generation,roundCount,targetCount,timestamps,macs,samplesOffset};
}

async function fetchDay(file,cacheKey){
 const suffix=file.immutable?`?revision=${encodeURIComponent(file.revision||file.bytes||1)}`:`?generation=${encodeURIComponent(cacheKey)}`;
 const response=await fetch(`zd1200-ping-monitor-daily/${file.file}${suffix}`,{cache:file.immutable?'force-cache':'no-store'});
 if(!response.ok)throw new Error(`Unable to load ${file.file}: HTTP ${response.status}`);
 const compressed=await response.arrayBuffer();
 if(typeof DecompressionStream==='undefined')throw new Error('This browser cannot decompress Ping Monitor history.');
 const stream=new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
 return parseDay(await new Response(stream).arrayBuffer());
}

function percentile(histograms,bucket,width,total,rank){
 if(!total)return null;let wanted=Math.ceil(total*rank),seen=0,offset=bucket*width;
 for(let value=1;value<width;value++){seen+=histograms[offset+value];if(seen>=wanted)return value}
 return null;
}

async function aggregate(job){
 const targetIndex=new Map(job.targetMacs.map((mac,index)=>[mac,index]));
 const selected=new Set(job.selectedMacs),targetCount=job.targetMacs.length;
 const buckets=Math.ceil((job.rangeEnd-job.rangeStart)/job.bucketSeconds),histogramWidth=job.timeoutMs+1;
 const attempts=new Uint32Array(buckets),replies=new Uint32Array(buckets),contributors=new Uint32Array(buckets);
 const contributorMarks=new Uint32Array(buckets*targetCount),histograms=new Uint32Array(buckets*histogramWidth);
 const summaryAttempts=new Uint32Array(targetCount),summaryReplies=new Uint32Array(targetCount);
 const latestTimestamp=new Uint32Array(targetCount),latestCode=new Uint8Array(targetCount),latestLatency=new Uint16Array(targetCount);
 let next=0,completed=0;
 async function run(){
  while(next<job.files.length){
   const file=job.files[next++],day=await fetchDay(file,job.generation);
   for(let target=0;target<day.targetCount;target++){
    const mac=day.macs[target],metadataIndex=targetIndex.get(mac),timelineSelected=selected.has(mac),sampleBase=day.samplesOffset+target*day.roundCount;
    if(metadataIndex===undefined&&!timelineSelected)continue;
    for(let round=0;round<day.roundCount;round++){
     const timestamp=day.timestamps[round],code=day.bytes[sampleBase+round];if(code===0)continue;
     if(metadataIndex!==undefined&&timestamp>=job.summaryStart&&timestamp<job.summaryEnd){
      summaryAttempts[metadataIndex]++;if(code!==255)summaryReplies[metadataIndex]++;
      if(timestamp>=latestTimestamp[metadataIndex]){latestTimestamp[metadataIndex]=timestamp;latestCode[metadataIndex]=code;latestLatency[metadataIndex]=code===255?0:day.codebook[code-1]}
     }
     if(!timelineSelected||timestamp<job.rangeStart||timestamp>=job.rangeEnd)continue;
     const bucket=Math.floor((timestamp-job.rangeStart)/job.bucketSeconds);attempts[bucket]++;
     const mark=bucket*targetCount+(metadataIndex===undefined?0:metadataIndex);
     if(metadataIndex!==undefined&&!contributorMarks[mark]){contributorMarks[mark]=1;contributors[bucket]++}
     if(code!==255){const latency=day.codebook[code-1];replies[bucket]++;histograms[bucket*histogramWidth+latency]++}
    }
   }
   completed++;postMessage({type:'progress',id:job.id,completed,total:job.files.length});
  }
 }
 await Promise.all(Array.from({length:Math.min(3,job.files.length)},run));
 const points=new Array(buckets);
 for(let bucket=0;bucket<buckets;bucket++)points[bucket]={attempts:attempts[bucket],replies:replies[bucket],contributors:contributors[bucket],p50:percentile(histograms,bucket,histogramWidth,replies[bucket],.5),p99:percentile(histograms,bucket,histogramWidth,replies[bucket],.99)};
 return{type:'result',id:job.id,points,summary:{attempts:Array.from(summaryAttempts),replies:Array.from(summaryReplies),latestTimestamp:Array.from(latestTimestamp),latestCode:Array.from(latestCode),latestLatency:Array.from(latestLatency)}};
}

async function sparklines(job){
 const targetPosition=new Map(job.selectedMacs.map((mac,index)=>[mac,index])),targetCount=job.selectedMacs.length;
 const buckets=24,width=job.timeoutMs+1,attempts=new Uint32Array(targetCount*buckets),failures=new Uint32Array(targetCount*buckets),histograms=new Uint32Array(targetCount*buckets*width);
 let next=0;
 async function run(){
  while(next<job.files.length){
   const file=job.files[next++],day=await fetchDay(file,job.generation);
   for(let target=0;target<day.targetCount;target++){
    const position=targetPosition.get(day.macs[target]);if(position===undefined)continue;
    const sampleBase=day.samplesOffset+target*day.roundCount;
    for(let round=0;round<day.roundCount;round++){
     const timestamp=day.timestamps[round],code=day.bytes[sampleBase+round];if(!code||timestamp<job.start||timestamp>=job.end)continue;
     const bucket=Math.floor((timestamp-job.start)/3600),offset=position*buckets+bucket;attempts[offset]++;
     if(code===255)failures[offset]++;else histograms[offset*width+day.codebook[code-1]]++;
    }
   }
  }
 }
 await Promise.all(Array.from({length:Math.min(2,job.files.length)},run));
 const values=job.selectedMacs.map((mac,target)=>({mac,points:Array.from({length:buckets},(_,bucket)=>{
  const offset=target*buckets+bucket,total=attempts[offset],failure=failures[offset],replies=total-failure;
  return[total,failure,percentile(histograms,offset,width,replies,.5),percentile(histograms,offset,width,replies,.99)];
 })}));
 return{type:'sparklines',id:job.id,start:job.start,values};
}

self.onmessage=event=>{
 const job=event.data;if(!job)return;
 const work=job.type==='aggregate'?aggregate(job):job.type==='sparklines'?sparklines(job):null;if(!work)return;
 work.then(result=>postMessage(result)).catch(error=>postMessage({type:'error',id:job.id,message:error.message}));
};
