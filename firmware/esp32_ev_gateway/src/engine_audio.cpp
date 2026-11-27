#if EV_F2001_AUDIO
#include <Arduino.h>
#include <driver/i2s_std.h>
#include "engine_audio.h"

extern "C" const int16_t f2001_samples[][32768];

namespace {
constexpr int blockFrames = 128, sampleRate = 32000;
constexpr float blockTime = float(blockFrames) / sampleRate;
constexpr float gearTop[] = {20,30,41,52,63,73,78};
struct Input { float rpm, pedal; uint32_t time; bool valid; };
QueueHandle_t inputQueue;
i2s_chan_handle_t channel;

// Reference RPM and gain crossover points from the F2001 cockpit bank.
struct Layer { float ref, start, full, endFull, end; };
constexpr Layer layers[] = {
    {4000,0,1,4200,5000}, {8400,4200,5000,8500,10000},
    {10700,8500,10000,12100,13400}, {13800,12100,13400,14700,15800},
    {17000,14700,15800,16800,17300}, {17000,16800,17300,20000,22000},
    {8000,4200,5000,7700,9200}, {10300,7700,9200,11000,12500},
    {13200,11000,12500,13800,15600}, {17400,13800,15600,20000,22000}
};
struct Mixer {
    float phase[10]{}, gain[10]{};
    int gear=0, shot=32768;
    float hold=0, shift=0, duration=.16f, from=4000, rpm=4000, load=0;
    bool live=false;
    void render(int16_t *out, Input in, bool fresh) {
        const float speed=in.rpm*.01657f, pedal=in.pedal*.01f;
        if(fresh && !live){
            gear=0;
            while(gear<6 && speed>gearTop[gear]) ++gear;
            shift=hold=0; load=pedal; shot=32768;
        }
        live=fresh;
        hold=fmaxf(0,hold-blockTime); shift=fmaxf(0,shift-blockTime);
        const bool up=gear<6 && speed>=gearTop[gear] && pedal>.15f;
        const bool down=gear>0 && speed<.80f*gearTop[gear-1];
        if(fresh && hold==0 && (up || down)){
            gear+=up?1:-1; from=rpm; duration=up?.16f:.145f;
            shift=duration; hold=.38f; if(down) shot=0;
        }
        const float target=constrain(speed*18000/gearTop[gear],4000.f,18000.f);
        const float t=1-shift/duration;
        rpm=from+(target-from)*t*t*(3-2*t);
        const float blip=shift>0 && target>from ? fminf(.82f,(target-from)/4800)*sinf(PI*t):0;
        load+=(pedal+(1-pedal)*blip-load)*.044f;
        float mix[blockFrames]{};
        for(unsigned k=0;k<10;++k){
            const Layer &l=layers[k];
            float band=constrain((rpm-l.start)/(l.full-l.start),0.f,1.f)
                      *constrain((l.end-rpm)/(l.end-l.endFull),0.f,1.f);
            float power=k==0?.8f:(k<=5?sqrtf(load):sqrtf(1-load));
            float desired=fresh?band*power:0;
            float next=gain[k]+.20f*(desired-gain[k]);
            if(next<.0001f && desired==0){ gain[k]=0; phase[k]=0; continue; }
            float step=rpm/l.ref, delta=(next-gain[k])/blockFrames;
            for(int i=0;i<blockFrames;++i){
                int j=int(phase[k]);float fraction=phase[k]-j;
                float a=f2001_samples[k][j], b=f2001_samples[k][(j+1)&32767];
                mix[i]+=(a+(b-a)*fraction)*(gain[k]+delta*i);
                phase[k]+=step;if(phase[k]>=32768)phase[k]-=32768;
            }
            gain[k]=next;
        }
        if(!fresh)shot=32768;
        for(int i=0;i<blockFrames;++i){
            if(shot<32768)mix[i]+=.32f*f2001_samples[10][shot++];
            int16_t sample=int16_t(constrain(mix[i]*.62f,-32767.f,32767.f));
            out[2*i]=out[2*i+1]=sample;
        }
    }
};
void audioTask(void*){
    Mixer mixer;
    Input in{};
    int16_t output[blockFrames*2];
    for(;;){
        xQueueReceive(inputQueue,&in,0);
        mixer.render(output,in,in.valid && millis()-in.time<300);
        size_t written=0;
        if(i2s_channel_write(channel,output,sizeof(output),&written,100)!=ESP_OK || written!=sizeof(output))
            vTaskDelay(pdMS_TO_TICKS(10));
    }
}
}
void setEngineAudioInput(float rpm,float pedal,bool valid){
    if(inputQueue){
        Input in{rpm,constrain(pedal,0.f,100.f),millis(),valid};
        xQueueOverwrite(inputQueue,&in);
    }
}
void startEngineAudio(){
    i2s_chan_config_t bus=I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO,I2S_ROLE_MASTER);
    bus.dma_desc_num=3;bus.dma_frame_num=blockFrames;
    i2s_std_config_t format{};
    format.clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(sampleRate);
    format.slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,I2S_SLOT_MODE_STEREO);
    format.gpio_cfg.mclk=I2S_GPIO_UNUSED;format.gpio_cfg.din=I2S_GPIO_UNUSED;
    format.gpio_cfg.bclk=GPIO_NUM_5;format.gpio_cfg.ws=GPIO_NUM_6;format.gpio_cfg.dout=GPIO_NUM_7;
    inputQueue=xQueueCreate(1,sizeof(Input));
    if(inputQueue && i2s_new_channel(&bus,&channel,nullptr)==ESP_OK &&
       i2s_channel_init_std_mode(channel,&format)==ESP_OK && i2s_channel_enable(channel)==ESP_OK &&
       xTaskCreatePinnedToCore(audioTask,"F2001 audio",4096,nullptr,1,nullptr,1)==pdPASS){
        Serial.println("# F2001 I2S ready BCLK=5 WS=6 DOUT=7"); return;
    }
    if(channel){i2s_channel_disable(channel);i2s_del_channel(channel);}
    if(inputQueue){vQueueDelete(inputQueue);inputQueue=nullptr;}
    Serial.println("# F2001 I2S unavailable");
}
#endif
