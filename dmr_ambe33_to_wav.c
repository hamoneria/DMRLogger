#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include "mbelib.h"

static const int rW[36] = {0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,2,0,2,0,2,0,2,0,2,0,2,0,2};
static const int rX[36] = {23,10,22,9,21,8,20,7,19,6,18,5,17,4,16,3,15,2,14,1,13,0,12,10,11,9,10,8,9,7,8,6,7,5,6,4};
static const int rY[36] = {0,2,0,2,0,2,0,2,0,3,0,3,1,3,1,3,1,3,1,3,1,3,1,3,1,3,1,3,1,3,1,3,1,3,1,3};
static const int rZ[36] = {5,3,4,2,3,1,2,0,1,13,0,12,22,11,21,10,20,9,19,8,18,7,17,6,16,5,15,4,14,3,13,2,12,1,11,0};

static void le16(FILE *f, uint16_t v){ fputc(v&255,f); fputc((v>>8)&255,f); }
static void le32(FILE *f, uint32_t v){ fputc(v&255,f); fputc((v>>8)&255,f); fputc((v>>16)&255,f); fputc((v>>24)&255,f); }

static void wav_header(FILE *f, int samples, int rate){
    uint32_t data = samples*2;
    fwrite("RIFF",1,4,f); le32(f,36+data); fwrite("WAVEfmt ",1,8,f); le32(f,16);
    le16(f,1); le16(f,1); le32(f,rate); le32(f,rate*2); le16(f,2); le16(f,16);
    fwrite("data",1,4,f); le32(f,data);
}

static void fill_frame(char fr[4][24], const uint8_t *dibits, int *idx, int start, int count){
    for(int i=start; i<start+count && *idx<132; i++){
        uint8_t d = dibits[(*idx)++];
        fr[rW[i]][rX[i]] = (d >> 1) & 1;
        fr[rY[i]][rZ[i]] = d & 1;
    }
}

static void decode_ambe(char ambe_fr[4][24], short *audio_out, int *errs, int *errs2,
                        mbe_parms *cur_mp, mbe_parms *prev_mp, mbe_parms *prev_mp_enhanced){
    char ambe_d[49]; char err_str[64];
    memset(ambe_d,0,sizeof(ambe_d)); memset(err_str,0,sizeof(err_str));
    *errs = mbe_eccAmbe3600x2450C0(ambe_fr);
    mbe_demodulateAmbe3600x2450Data(ambe_fr);
    *errs2 = *errs + mbe_eccAmbe3600x2450Data(ambe_fr, ambe_d);
    mbe_processAmbe2450Data(audio_out, errs, errs2, err_str, ambe_d, cur_mp, prev_mp, prev_mp_enhanced, 3);
}

static void write_decoded_frame(FILE *out, char fr[4][24], mbe_parms *cur, mbe_parms *prev, mbe_parms *enh, long *samples, long *errs_sum, long *errs2_sum){
    short audio[160]; int errs=0, errs2=0;
    decode_ambe(fr, audio, &errs, &errs2, cur, prev, enh);
    *errs_sum += errs; *errs2_sum += errs2;
    for(int i=0;i<160;i++){
        int v = audio[i] * 2;
        if(v>32767) v=32767; if(v<-32768) v=-32768;
        le16(out, (uint16_t)(int16_t)v);
        (*samples)++;
    }
}

int main(int argc, char **argv){
    if(argc != 3){ fprintf(stderr,"usage: %s input.ambe33 output.wav\n", argv[0]); return 2; }
    FILE *in=fopen(argv[1],"rb"); if(!in){ perror("open input"); return 1; }
    fseek(in,0,SEEK_END); long len=ftell(in); fseek(in,0,SEEK_SET);
    uint8_t *data=malloc(len); if(!data){ perror("malloc"); return 1; }
    if(fread(data,1,len,in)!=(size_t)len){ perror("read"); return 1; }
    fclose(in);
    long payloads = len / 33;
    long total_samples = payloads * 3 * 160;
    FILE *out=fopen(argv[2],"wb"); if(!out){ perror("open output"); return 1; }
    wav_header(out, total_samples, 8000);
    mbe_parms *cur=calloc(1,sizeof(mbe_parms)), *prev=calloc(1,sizeof(mbe_parms)), *enh=calloc(1,sizeof(mbe_parms));
    mbe_initMbeParms(cur, prev, enh);
    long samples=0, errs_sum=0, errs2_sum=0;
    for(long off=0; off+33<=len; off+=33){
        uint8_t dibits[132];
        for(int i=0;i<33;i++){
            uint8_t b=data[off+i];
            dibits[i*4+0]=(b>>6)&3; dibits[i*4+1]=(b>>4)&3; dibits[i*4+2]=(b>>2)&3; dibits[i*4+3]=b&3;
        }
        char fr1[4][24]={{0}}, fr2[4][24]={{0}}, fr3[4][24]={{0}};
        int idx=0;
        fill_frame(fr1,dibits,&idx,0,36);
        fill_frame(fr2,dibits,&idx,0,18);
        idx += 24;
        fill_frame(fr2,dibits,&idx,18,18);
        fill_frame(fr3,dibits,&idx,0,36);
        write_decoded_frame(out,fr1,cur,prev,enh,&samples,&errs_sum,&errs2_sum);
        write_decoded_frame(out,fr2,cur,prev,enh,&samples,&errs_sum,&errs2_sum);
        write_decoded_frame(out,fr3,cur,prev,enh,&samples,&errs_sum,&errs2_sum);
    }
    fclose(out); free(data); free(cur); free(prev); free(enh);
    fprintf(stderr,"decoded payloads=%ld samples=%ld seconds=%.2f errs=%ld errs2=%ld\n", payloads, samples, samples/8000.0, errs_sum, errs2_sum);
    return 0;
}
