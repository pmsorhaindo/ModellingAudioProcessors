# Virtual-analog and antialiasing techniques

General methods for turning the circuit model into a fast, alias-free plugin.

## Antialiasing for nonlinearities

- **Reducing the Aliasing of Nonlinear Waveshaping Using Continuous-Time Convolution.** J. D. Parker, V. Zavalishin, E. Le Bivic. *Proc. DAFx-16*, Brno, 2016. <https://www.dafx.de/paper-archive/2016/dafxpapers/20-DAFx-16_paper_41-PN.pdf>. *Open.*
  - The original antiderivative antialiasing (ADAA) paper. It suppresses aliasing from a static waveshaper with little or no oversampling.
  - Directly applicable if the valve stage is reduced to a static curve that depends on bias.
  - Companion code: <https://github.com/julian-parker/DAFX-AntiAliasing>.
- **Antiderivative Antialiasing for Memoryless Nonlinearities.** S. Bilbao, F. Esqueda, J. D. Parker, V. Välimäki. *IEEE Signal Processing Letters* 24(7), 2017. DOI [10.1109/LSP.2017.2675541](https://doi.org/10.1109/LSP.2017.2675541). *Paywalled; free author copy:* <https://aaltodoc.aalto.fi/server/api/core/bitstreams/87839e85-c165-4bd9-aeed-7c96ff64026a/content> (Aaltodoc; accepted manuscript). Also on Edinburgh Research Explorer: <https://www.pure.ed.ac.uk/ws/files/34115216/bilbao_pdf.pdf> (peer-reviewed version).
  - Generalises ADAA to higher orders and analyses its accuracy. Use it when choosing first- or second-order ADAA.
- **Aliasing Reduction in Clipped Signals.** F. Esqueda, S. Bilbao, V. Välimäki. *IEEE Trans. Signal Processing* 64(20), 2016. DOI [10.1109/TSP.2016.2585091](https://doi.org/10.1109/TSP.2016.2585091). *Paywalled; free author copy:* <https://aaltodoc.aalto.fi/server/api/core/bitstreams/e0b34035-b20e-457e-85c3-e42c4951363f/content> (Aaltodoc; accepted manuscript, revised June 2016). Also on Edinburgh Research Explorer: <https://www.pure.ed.ac.uk/ws/files/26997332/07499828.pdf> (peer-reviewed version).
  - Band-limited correction (BLAMP) for hard clipping. Relevant to the hard, gated P2/P3 behaviour and to starved-bias cutoff.
- **Antiderivative Antialiasing for Stateful Systems.** M. Holters. *Applied Sciences* 10(1):20, 2019. DOI [10.3390/app10010020](https://doi.org/10.3390/app10010020). *Open* (MDPI).
  - Extends ADAA to nonlinear systems that have state, such as a DK-method model with coupling capacitors.

## Oversampling

- **Oversampling for Nonlinear Waveshaping: Choosing the Right Filters.** J. Kahles, F. Esqueda, V. Välimäki. *J. Audio Eng. Soc.* 67(6), 2019. DOI [10.17743/jaes.2019.0012](https://doi.org/10.17743/jaes.2019.0012). *Open* (marked "Free to read" in the AES E-Library).
  - Compares interpolation and decimation filters for 8× oversampled waveshaping. Use it to size the oversampler against CPU.

## Filters and discretisation

- **The Art of VA Filter Design (rev. 2.1.2).** V. Zavalishin, Native Instruments, 2020. *Open.* Archived: <https://web.archive.org/web/20260201221722/https://www.native-instruments.com/fileadmin/ni_media/downloads/pdf/VAFilterDesign_2.1.2.pdf>. The original NI URL now returns 404.
  - Zero-delay-feedback / TPT discretisation. Covers the 12 dB/octave output low-pass and the coupling and tone networks between valve stages.
- **Wave Digital Filters: Theory and Practice.** A. Fettweis. *Proceedings of the IEEE* 74(2), 1986. DOI [10.1109/PROC.1986.13458](https://doi.org/10.1109/PROC.1986.13458). *Paywalled.*
  - The foundational WDF reference: adaptors, port resistances, passivity.
- **Tutorial on Wave Digital Filters.** D. T. Yeh, CCRMA DSP Seminar slides, January 2008. <https://ccrma.stanford.edu/~dtyeh/papers/wdftutorial.pdf>. *Open.*
  - A short, practical introduction before tackling Fettweis or Werner.

## Tooling

- **chowdsp_wdf: An Advanced C++ Library for Wave Digital Circuit Modelling.** J. Chowdhury. arXiv:2210.12554, 2022. <https://arxiv.org/abs/2210.12554>. *Open.*
  - A header-only C++ WDF library with SIMD support and R-type adaptors. A strong candidate for the real-time core or for prototyping.
- **chowdsp_wdf (source code).** Chowdhury DSP, GitHub. <https://github.com/Chowdhury-DSP/chowdsp_wdf>
  - Check its licence before it ships in a product.
