// Straight port of dk/solver.py + stage.py per-sample kernel (6 states, 2 NL ports), polyphase FIR up/down.
#include <stdio.h>
#include <math.h>
#include <string.h>
#include <time.h>
#include "mats.h"
#ifndef TOL
#define TOL 1e-9
#endif
#ifndef R
#define R double
#endif
#if R_IS_FLOAT
#define EXP expf
#define LOG1P log1pf
#define LOG logf
#define POW powf
#define FABS fabsf
#else
#define EXP exp
#define LOG1P log1p
#define LOG log
#define POW pow
#define FABS fabs
#endif
static const R Gk=2.242e-3, MU=103.2, GAM=1.26, CC=3.40, GG=6.177e-4, XI=1.314, CG=9.901, IG0=8.025e-8;
static long ncur=0;
static inline R sp(R x){ return (x>0?x:0)+LOG1P(EXP(-FABS(x))); }
static inline R sg(R x){ if(x>=0) return 1/(1+EXP(-x)); R e=EXP(x); return e/(1+e);}
static inline void cur(R vg,R va,R*ig,R*ia,R J[4]){ ncur++;
  R zk=CC*(va/MU+vg), sk=sp(zk)/CC, ik=Gk*POW(sk,GAM), dik=Gk*GAM*POW(sk,GAM-1)*sg(zk);
  R zg=CG*vg, s2=sp(zg)/CG, g=GG*POW(s2,XI)+IG0, dg=GG*XI*POW(s2,XI-1)*sg(zg);
  *ig=g; *ia=ik-g; J[0]=dg; J[1]=0; J[2]=dik-dg; J[3]=dik/MU; }
static const R VT=1.0/9.901;
static inline R pnj(R vn,R vo){ if(vn>0 && FABS(vn-vo)>2*VT){ if(vo>0){R a=1+(vn-vo)/VT; return a>0?vo+VT*LOG(a):0;} return vn>VT?VT*LOG(vn/VT):vn;} return vn; }
typedef struct { R x[6], v0, v1; const double *A,*Bv,*Bh,*C,*G,*Hv,*Hh,*K,*D,*Ev,*Eh,*F; long it; } St;
static inline R step(St*s, R u){
  R p0=s->Hv[0]*u+s->Hh[0], p1=s->Hv[1]*u+s->Hh[1];
  for(int j=0;j<6;j++){ p0+=s->G[j]*s->x[j]; p1+=s->G[6+j]*s->x[j]; }
  const double*K=s->K; R v0=s->v0,v1=s->v1, ig,ia,J[4];
  cur(v0,v1,&ig,&ia,J); R f0=p0-K[0]*ig-K[1]*ia-v0, f1=p1-K[2]*ig-K[3]*ia-v1;
  for(int it=1;it<=50;it++){ s->it++;
    R a=-(K[0]*J[0]+K[1]*J[2])-1, b=-(K[0]*J[1]+K[1]*J[3]), c=-(K[2]*J[0]+K[3]*J[2]), d=-(K[2]*J[1]+K[3]*J[3])-1;
    R det=a*d-b*c, d0=-(d*f0-b*f1)/det, d1=-(-c*f0+a*f1)/det;
    R n0=pnj(v0+d0,v0), s0=n0-v0, s1=d1*(d0!=0?(n0-v0)/d0:1);
    R fn=fmax(FABS(f0),FABS(f1)), lam=1, g0=0,g1=0,gig=0,gia=0,gJ[4];
    for(int k=0;k<10;k++){ cur(v0+lam*s0,v1+lam*s1,&gig,&gia,gJ); g0=p0-K[0]*gig-K[1]*gia-(v0+lam*s0); g1=p1-K[2]*gig-K[3]*gia-(v1+lam*s1);
      if(fmax(FABS(g0),FABS(g1))<fn || fn<TOL) break; lam*=0.5; }
    R dv=fmax(FABS(lam*s0),FABS(lam*s1)); v0+=lam*s0; v1+=lam*s1; f0=g0; f1=g1; ig=gig; ia=gia; memcpy(J,gJ,sizeof J);
    if(dv<TOL && fmax(FABS(f0),FABS(f1))<TOL) break; }
  // float can't reach TOL at 200 V: tolerance acts as an iteration cap there (reported)
  R y=s->Ev[0]*u+s->Eh[0]+s->F[0]*ig+s->F[1]*ia; for(int j=0;j<6;j++) y+=s->D[j]*s->x[j];
  R xn[6]; for(int i=0;i<6;i++){ R t=s->Bv[i]*u+s->Bh[i]+s->C[2*i]*ig+s->C[2*i+1]*ia; for(int j=0;j<6;j++) t+=s->A[6*i+j]*s->x[j]; xn[i]=t; }
  memcpy(s->x,xn,sizeof xn); s->v0=v0; s->v1=v1; return y;
}
#define RUN(OS) { St s={0}; for(int i=0;i<6;i++) s.x[i]=X0_##OS[i]; s.v0=V0_##OS[0]; s.v1=V0_##OS[1]; \
  s.A=A_##OS;s.Bv=Bv_##OS;s.Bh=Bh_##OS;s.C=C_##OS;s.G=G_##OS;s.Hv=Hv_##OS;s.Hh=Hh_##OS;s.K=K_##OS;s.D=D_##OS;s.Ev=Ev_##OS;s.Eh=Eh_##OS;s.F=F_##OS; \
  bench(OS, &s, FIR_##OS, sizeof(FIR_##OS)/sizeof(double)); }
#define N 48000
static R hist_in[4096], hist_out[4096];
static void bench(int OS, St*s, const double*h, int L, double amp){}
static double now(){ struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }
static void run(int OS, St*s0, const double*hd, int L, double amp){
  R h[1024]; for(int i=0;i<L;i++) h[i]=(R)hd[i];
  int P=(L+OS-1)/OS; // taps per phase
  double best=1e9, bestfir=1e9; long it=0; R sink=0;
  for(int rep=0;rep<5;rep++){ St s=*s0; ncur=0;
    R inb[1024]={0}, outb[1024]={0}; int ip=0, op=0; R dc=0;
    double t0=now();
    for(int n=0;n<N;n++){ R x=(R)(amp*sin(2*M_PI*1000.0*n/48000.0));
      inb[ip]=x; 
      for(int ph=0;ph<OS;ph++){ R u=0; for(int k=0;k<P;k++){ int t=ph+k*OS; if(t<L) u+=h[t]*inb[(ip-k)&1023]; } u*=OS;
        R y=step(&s,u); if(n==0&&ph==0) dc=y; outb[op]=y-dc; op=(op+1)&1023; }
      R acc=0; for(int k=0;k<L;k++) acc+=h[k]*outb[(op-1-k)&1023]; sink+=acc;
      ip=(ip+1)&1023; }
    double t1=now(); if(t1-t0<best){best=t1-t0; it=s.it;}
    // FIR alone
    double t2=now(); for(int n=0;n<N;n++){ inb[ip]=(R)n; for(int ph=0;ph<OS;ph++){R u=0; for(int k=0;k<P;k++){int t=ph+k*OS; if(t<L) u+=h[t]*inb[(ip-k)&1023];} outb[op]=u; op=(op+1)&1023;} R acc=0; for(int k=0;k<L;k++) acc+=h[k]*outb[(op-1-k)&1023]; sink+=acc; ip=(ip+1)&1023;}
    double t3=now(); if(t3-t2<bestfir) bestfir=t3-t2;
  }
  printf("%-6s os=%2d amp=%4.1fV  %7.1f ns/base-sample  (FIR %5.1f ns)  mean NR iters/internal sample %.2f  CPU%% of one core @48k: %.2f%%  [sink %g]\n",
    sizeof(R)==4?"float":"double", OS, amp, best/N*1e9, bestfir/N*1e9, (double)it/(N*OS), best/N*48000*100, (double)sink);
}
#undef RUN
#define RUN(OS,amp) { St s={0}; for(int i=0;i<6;i++) s.x[i]=X0_##OS[i]; s.v0=V0_##OS[0]; s.v1=V0_##OS[1]; \
  s.A=A_##OS;s.Bv=Bv_##OS;s.Bh=Bh_##OS;s.C=C_##OS;s.G=G_##OS;s.Hv=Hv_##OS;s.Hh=Hh_##OS;s.K=K_##OS;s.D=D_##OS;s.Ev=Ev_##OS;s.Eh=Eh_##OS;s.F=F_##OS; \
  run(OS,&s,FIR_##OS,sizeof(FIR_##OS)/sizeof(double),amp); }
int main(){ double amps[]={0.5,4.0}; for(int a=0;a<2;a++){ RUN(2,amps[a]) RUN(4,amps[a]) RUN(8,amps[a]) RUN(16,amps[a]) } return 0; }
