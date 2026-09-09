# PrajnaanaOpen

PrajnaanaOpen is a collection of selected software tools, algorithms, and reference implementations developed by **Prajnaana** as part of our embedded product engineering work — shared openly because engineering capability is best demonstrated through working technology.

The projects are made available as open-source software so that engineers and organizations can explore practical implementations, experiment with the technology, reuse components, evaluate approaches for their own applications, and build prototypes and proof-of-concepts.

Our focus is on practical solutions for **Automotive, Industrial, Medical, and Connected Device** applications.

Please refer to the individual project directories for detailed documentation, including capabilities, prerequisites, installation, usage, examples, implementation details, and project-specific licensing information.

---

## 📦 Projects

| Project | What it is |
|:---|:---|
| **[PPG_Signal_Processing](PPG_Signal_Processing)** | Heart rate, heart-rate variability, respiratory rate and respiratory-rate variability from a single photoplethysmogram channel. Portable C99 with no dependency beyond `libm`; one binary serves neonates through adults, and a live desktop monitor and an Android demo app come with it. |
| **[RPC_ReferenceFramework](RPC_ReferenceFramework)** | A multi-core RPC framework in C. A generator reads your function prototypes and emits the marshalling stubs and dispatch tables, so code on one core, process or machine calls a function that lives on another without hand-writing socket or buffer handling. Transport is TCP, and two Android projects put a core on a phone behind an AIDL service. |
| **[Vehicle_CAN_Simulator](Vehicle_CAN_Simulator)** | A vehicle simulator that drives itself along real road routes and emits realistic CAN bus traffic while it does, so automotive electronics can be developed and tested without a car, a driver, or a road. |

Each project directory carries its own documentation, build instructions and licence.

---

## 🤝 Need Engineering Support?

These projects are open-source reference implementations. **Moving one into a
production product may require application-specific adaptation, integration,
validation and optimization**. Prajnaana Technologies has practical experience
across these stages of embedded product engineering. If a project in this
repository is relevant to your product or engineering challenge, we would be
happy to discuss how Prajnaana can help adapt, integrate, and productize the
technology for your needs.

**Prajnaana Technologies**
Automotive • Industrial • Medical • Connected Devices • Embedded Systems

**[www.prajnaanatech.com →](https://www.prajnaanatech.com/)**

---

## 📜 License

Each project directory in this repository carries its own `LICENSE` file, and those are the terms that govern that project. They are not the same for every project, so read the one beside the code you intend to use.

Individual projects may also contain additional notices regarding third-party libraries, data, or other dependencies. Please review the project-specific documentation before redistribution or commercial use.

---

### ⭐ Explore. Experiment. Build.

We hope these projects are useful to the engineering community.

If you find a project useful, consider giving it a ⭐ on GitHub or sharing it with engineers who may benefit from it.
