/* GPL-3.0-or-later. Numerical port of Pix::UpdateSuperpixelMapping,
 * Copyright (c) 2013 Timothy Gerstner. See LICENSE and core.py provenance.
 * C ABI only; OpenCV Lab conversion remains in the Python host.
 */
#include <cmath>
#include <algorithm>
#include <limits>
#include <vector>
extern "C" __declspec(dllexport) void pia_mapping(
    const float* lab, const double* pos, const double* color,
    int width, int height, int ow, int oh, int range, double m, int* regions) {
    std::vector<double> best(width*height, std::numeric_limits<double>::infinity());
    std::fill(regions, regions+width*height, -1);
    for(int s=0;s<ow*oh;++s){
        double x=pos[s*2], y=pos[s*2+1];
        int x0=(int)std::max(0.,x-range), x1=std::min(width-1,(int)(x+range));
        int y0=(int)std::max(0.,y-range), y1=std::min(height-1,(int)(y+range));
        for(int yy=y0;yy<=y1;++yy) for(int xx=x0;xx<=x1;++xx){
            int p=yy*width+xx;
            double dc=0;
            for(int c=0;c<3;++c){double d=lab[p*3+c]-color[s*3+c];dc+=d*d;}
            double dx=xx-x,dy=yy-y;
            double cost=std::sqrt(dc)+m/range*std::sqrt(dx*dx+dy*dy);
            if(cost<best[p]){best[p]=cost;regions[p]=s;}
        }
    }
    // Safety correction: original vec2idx((-1,-1)) is -ow-1, not -1.
    for(int y=0;y<height;++y) for(int x=0;x<width;++x)
        if(regions[y*width+x]<0) regions[y*width+x]=(y*oh/height)*ow+x*ow/width;
}
