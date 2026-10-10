# Valve (tube) modelling papers

The Culture Vulture's character comes from the **6AS6 / 5725 dual-control pentode**. The front-panel Function switch rewires it as a triode or as a pentode. The input stage is a pentode (EF86, or M8100 / 5654 in later units), and the output stage is a 5963 twin triode. So the project needs:

- a **pentode** model (with screen-grid and, ideally, suppressor-grid dependence),
- a **triode** model (for T mode and the output stage),
- a way to solve the circuit around them.

Access: *Open* means free full text. *Paywalled* means you need a publisher subscription.

## Surveys and theses

- **A Review of Digital Techniques for Modeling Vacuum-Tube Guitar Amplifiers.** J. Pakarinen, D. T. Yeh. *Computer Music Journal* 33(2), 2009. DOI [10.1162/comj.2009.33.2.85](https://doi.org/10.1162/comj.2009.33.2.85). *Paywalled; no verified free copy.* Unpaywall, Semantic Scholar and OpenAlex list a free-to-read published version on MIT Press (<https://direct.mit.edu/comj/article/33/2/85/94251>). Its PDF download blocked automated fetching, so this is **unverified**.
  - Surveys the approaches, from static waveshapers to full circuit simulation. Use it to choose a method and justify the choice.
- **Digital Implementation of Musical Distortion Circuits by Analysis and Simulation.** D. T. Yeh, PhD dissertation, Stanford University (CCRMA), June 2009. <https://ccrma.stanford.edu/~dtyeh/papers/DavidYehThesissinglesided.pdf>. *Open.*
  - The core reference for nodal state-space (K-method / DK-style) simulation of distortion circuits, including valve stages, with real-time-oriented discretisation.
- **Virtual Analog Modeling of Audio Circuitry Using Wave Digital Filters.** K. J. Werner, PhD dissertation, Stanford University, 2016. <https://stacks.stanford.edu/file/druid:jy057cz8322/KurtJamesWernerDissertation-augmented.pdf> (record: <https://purl.stanford.edu/jy057cz8322>). *Open.*
  - The modern WDF framework. It shows how to handle topologies that aren't simple series/parallel, and multiple nonlinearities, which is what a multi-grid valve stage needs.

## Device models (triode and pentode equations)

- **Improved Vacuum Tube Models for SPICE Simulations (Parts 1 and 2).** N. Koren. First published in *Glass Audio* 8(5), 1996; the web version was updated in 2003. Part 1: <https://www.normankoren.com/Audio/Tubemodspice_article.html>. Part 2: <https://normankoren.com/Audio/Tubemodspice_article_2.html>. *Open.*
  - The standard phenomenological triode and pentode equations. They include screen-grid dependence and a triode-connection mode, with SPICE subcircuits.
  - The quickest way to get a working 6AS6 model: fit it to the datasheet curves and test in ngspice.
- **A Physically-Motivated Triode Model for Circuit Simulations.** K. Dempwolf, U. Zölzer. *Proc. DAFx-11*, Paris, 2011. <https://dafx.de/paper-archive/2011/Papers/76_e.pdf>. *Open.*
  - A smooth (continuously differentiable) triode model based on Child's law and fitted to measured data. Smoothness helps iterative (Newton-Raphson) solvers.
  - A candidate for T mode and the 5963 output stage.
- **Real-Time Simulation of a Guitar Power Amplifier.** I. Cohen, T. Hélie. *Proc. DAFx-10*, Graz, 2010. <https://dafx10.iem.at/proceedings/papers/CohenHelie_DAFx10_P45.pdf>. *Open.*
  - Gives Koren-model parameters for common pentodes and beam tetrodes and simulates a single-ended class-A pentode stage in real time.
  - The closest published workflow to modelling the 6AS6 in P1 mode.
- **Simulation of a Vacuum-Tube Push-Pull Guitar Power Amplifier.** J. Macák, J. Schimmel. *Proc. DAFx-11*, Paris, 2011. <https://www.dafx.de/paper-archive/2011/Papers/05_e.pdf>. *Open.*
  - Simulates a pentode stage at several levels of complexity, from an ideal transformer and constant load upward.
  - A useful guide for how much detail a pentode model needs.
- **A Quadric Surface Model of Vacuum Tubes for Virtual Analog Applications.** R. Giampiccolo, S. D'Angelo, A. Bernardini, A. Sarti. *Proc. DAFx23*, Copenhagen, 2023. <https://www.dafx.de/paper-archive/2023/DAFx23_paper_15.pdf>. *Open.*
  - A recent valve model proposed specifically for virtual-analog use. It covers triodes and pentodes in triode connection, which matches T mode.

## Circuit-level simulation: wave digital filters

- **Wave Digital Simulation of a Vacuum-Tube Amplifier.** M. Karjalainen, J. Pakarinen. *Proc. IEEE ICASSP*, 2006. DOI [10.1109/ICASSP.2006.1661235](https://doi.org/10.1109/ICASSP.2006.1661235). *Paywalled.*
  - An early WDF valve-stage model. It shows how to embed a triode nonlinearity in a WDF tree.
- **Enhanced Wave Digital Triode Model for Real-Time Tube Amplifier Emulation.** J. Pakarinen, M. Karjalainen. *IEEE Trans. Audio, Speech, and Language Processing* 18(4), 2010, pp. 738–746. DOI [10.1109/TASL.2009.2033306](https://doi.org/10.1109/TASL.2009.2033306). *Paywalled.*
  - A refined real-time WDF triode model. Check how it treats grid current, which matters when Overdrive slams the 6AS6 grid.
- **New Family of Wave-Digital Triode Models.** S. D'Angelo, J. Pakarinen, V. Välimäki. *IEEE Trans. Audio, Speech, and Language Processing* 21(2), 2013. DOI [10.1109/TASL.2012.2224340](https://doi.org/10.1109/TASL.2012.2224340). *Paywalled; free author copy:* <https://aaltodoc.aalto.fi/bitstreams/c94fca6e-463e-4f8e-a25d-9dce4aa4e8c8/download> (Aaltodoc, Aalto University repository; accepted manuscript, linked from the first author's site).
  - A newer family of WDF triode models that builds on the two above. Compare accuracy against cost before choosing one.
- **Resolving Wave Digital Filters with Multiple/Multiport Nonlinearities.** K. J. Werner, V. Nangia, J. O. Smith III, J. S. Abel. *Proc. DAFx-15*, Trondheim, 2015. <https://dafx.de/paper-archive/2015/DAFx-15_submission_54.pdf>. *Open.*
  - How to put a multiport nonlinearity into a WDF (a pentode with control and screen grids is one). Needed if we model the 6AS6 as a full multi-terminal device.

## Circuit-level simulation: nodal / DK-method state space

- **Automated Physical Modeling of Nonlinear Audio Circuits for Real-Time Audio Effects, Part I: Theoretical Development.** D. T. Yeh, J. S. Abel, J. O. Smith III. *IEEE Trans. Audio, Speech, and Language Processing* 18(4), 2010. DOI [10.1109/TASL.2009.2033978](https://doi.org/10.1109/TASL.2009.2033978). *Paywalled.* Author copy: <https://ccrma.stanford.edu/~dtyeh/papers/yeh10_taslp.pdf>.
  - Derives the nodal K-method / state-space formulation directly from a netlist.
- **Automated Physical Modeling of Nonlinear Audio Circuits for Real-Time Audio Effects, Part II: BJT and Vacuum Tube Examples.** D. T. Yeh. *IEEE TASLP* 20(4), 2012. DOI [10.1109/TASL.2011.2173677](https://doi.org/10.1109/TASL.2011.2173677). *Paywalled.* Author copy: <https://ccrma.stanford.edu/~dtyeh/papers/yeh12_taslp.pdf>.
  - Worked BJT and valve-stage examples of the Part I method.
- **Simulating Guitar Distortion Circuits Using Wave Digital and Nonlinear State-Space Formulations.** D. T. Yeh, J. O. Smith. *Proc. DAFx-08*, Espoo, 2008. <https://ccrma.stanford.edu/~dtyeh/papers/yeh08_dafx_sim.pdf>. *Open.*
  - A side-by-side comparison of WDF and state-space on the same circuits. Use it to choose between the two.
- **Physical Modelling of a Wah-wah Effect Pedal as a Case Study for Application of the Nodal DK Method to Circuits with Variable Parts.** M. Holters, U. Zölzer. *Proc. DAFx-11*, Paris, 2011. Abstract page: <https://www.dafx.de/paper-archive/details/hR2kI9NQ8b1kjRgzxNk0pw>. *Open* via the DAFx archive.
  - The DK method with potentiometers that change at runtime. Drive, Bias and Output are all pots, and Bias changes the operating point.

## Unverified (found but could not fetch)

- **Measures and Parameter Estimation of Triodes, for the Real-Time Simulation of a Multi-Stage Guitar Preamplifier.** I. Cohen, T. Hélie. AES 129th Convention, San Francisco, 2010, paper 8219 according to search results. Listed on HAL as `hal-00631747`, but the HAL page returned a bot check and could not be verified.
  - A measurement-driven way to fit triode parameters, relevant to the 5963.
