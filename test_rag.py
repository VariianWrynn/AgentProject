"""
End-to-end test for RAG Knowledge Base Pipeline
================================================
- Generates 3 test documents (industrial domain)
- Ingests them, runs 5 queries with Top-5 results + timing
- Tests incremental add of a 4th document, re-runs query
- Prints timing summary
- Cleans up test collection on exit
"""

import sys
import time
import tempfile
import os
from pathlib import Path

from rag_pipeline import RAGPipeline

# ---------------------------------------------------------------------------
# Test document content
# ---------------------------------------------------------------------------
DOCS = {
    "doc1_motor_maintenance.txt": """\
Industrial Motor Maintenance Guide
===================================
Electric motors are critical components in manufacturing plants. Regular
maintenance is essential to prevent unexpected downtime and extend motor life.

Scheduled Maintenance Tasks:
- Inspect motor bearings every 3 months for signs of wear or unusual noise.
- Check winding insulation resistance annually using a megohmmeter; values
  below 1 MΩ indicate degradation.
- Lubricate bearings according to manufacturer specs—over-lubrication causes
  overheating just as under-lubrication does.
- Clean cooling fins and ventilation passages quarterly to maintain proper
  airflow and prevent thermal buildup.
- Verify alignment between motor shaft and driven equipment; misalignment
  causes vibration and accelerates bearing failure.
- Inspect mounting bolts and base for looseness or corrosion.
- Check motor current draw under load; excessive current suggests mechanical
  overload, winding shorts, or low supply voltage.

Common Failure Modes:
1. Bearing failure — responsible for ~40% of motor failures. Early signs
   include high-frequency vibration and elevated bearing temperature.
2. Winding insulation breakdown — caused by moisture ingress, heat cycling,
   or voltage spikes. Monitor via insulation resistance testing.
3. Rotor eccentricity — produces 2× slip frequency sidebands around supply
   frequency in the vibration spectrum.
4. Shaft misalignment — produces 1×, 2×, and 3× running speed harmonics.

Thermal Management:
Motor life halves for every 10°C rise above rated winding temperature.
Install thermal protection relays (bimetallic or NTC thermistor) inside
motor windings. Set alarm at 130°C and trip at 155°C for Class F insulation.

Predictive Maintenance Tools:
- Vibration analysis (ISO 10816 limits for different machine classes)
- Infrared thermography to detect hot spots non-invasively
- Motor Current Signature Analysis (MCSA) for detecting rotor bar defects
- Oil analysis for gearbox-integrated drive systems
""",

    "doc2_hydraulic_systems.txt": """\
Hydraulic System Troubleshooting and Maintenance
=================================================
Hydraulic systems transmit power through pressurized fluid, enabling precise
force and motion control in heavy machinery, presses, and injection molding
equipment.

Common Hydraulic Failures:
1. Excessive heat — hydraulic fluid above 65°C degrades rapidly. Causes
   include undersized reservoir, blocked heat exchanger, continuous operation
   at high pressure, or contaminated fluid with high viscosity index.
2. Contamination — particles above ISO cleanliness code 16/14/11 cause
   valve spool sticking, pump wear, and seal extrusion. Install 10-micron
   return-line filters with visual clog indicators.
3. Cavitation — occurs when pump inlet pressure drops below fluid vapor
   pressure. Symptoms: rattling noise, erratic pressure, pitted pump surfaces.
   Fix: reduce inlet line restrictions, ensure adequate fluid level, use
   correct fluid viscosity for ambient temperature.
4. Internal leakage — worn pump, valve, or cylinder seals cause slow
   actuator movement under load. Measure flow with an inline flow meter.
5. Pump wear — measure volumetric efficiency; below 85% indicates worn
   pump requiring rebuild or replacement.

Fluid Maintenance:
- Sample fluid every 500 operating hours for ISO cleanliness and viscosity.
- Change fluid every 2000 hours or when viscosity deviates >10% from spec.
- Bleed air from system after fluid change to prevent foaming and
  compressibility issues.
- Flush system with compatible low-viscosity oil after pump replacement
  to remove metallic debris.

Seal and Hose Inspection:
- Check hydraulic hoses for external abrasion, cracking, or bulging annually.
- Replace hoses every 6 years regardless of visual condition (fatigue life).
- Inspect cylinder rod seals for weeping; fluid film is acceptable but
  dripping indicates imminent seal failure.

Pressure Relief Valves:
Set relief valves 10–15% above maximum working pressure. Test annually
by blocking actuator and verifying cracking pressure matches setpoint.
Stuck-open relief valves cause system pressure loss and overheating.
""",

    "doc3_safety_protocols.txt": """\
Factory Safety Protocols and Emergency Procedures
==================================================
Maintaining a safe working environment in industrial facilities requires
strict adherence to standardized safety protocols, personal protective
equipment (PPE) requirements, and emergency response procedures.

Required PPE by Work Zone:
- General plant floor: safety shoes (ASTM F2413), high-visibility vest,
  safety glasses (ANSI Z87.1).
- Machining areas: face shield over safety glasses, hearing protection
  (≥85 dB exposure), cut-resistant gloves when handling sharp stock.
- Electrical panels: arc flash PPE rated for calculated incident energy
  (minimum CAT 2 for panels ≤480V), insulated gloves Class 00 minimum.
- Chemical handling: chemical splash goggles, nitrile gloves, chemical
  apron; consult SDS for additional requirements.

Lockout/Tagout (LOTO) — OSHA 29 CFR 1910.147:
Before performing maintenance on any equipment with stored energy:
1. Notify affected employees.
2. Identify all energy sources (electrical, hydraulic, pneumatic, gravity).
3. Apply lockout device to each energy-isolating device.
4. Release or restrain all residual stored energy.
5. Verify zero-energy state with test equipment before beginning work.
6. Each worker applies their own lock—never one lock for multiple workers.

Emergency Shutdown Procedure (ESD):
1. Identify the hazard and announce emergency clearly.
2. Press nearest E-stop or pull the red emergency pull-cord.
3. Shut down process utilities (compressed air, cooling water) via
   manual isolation valves marked with red diamond labels.
4. Activate fire suppression if fire is present.
5. Evacuate all non-essential personnel to muster point A or B.
6. Contact plant emergency response team via intercom channel 9.
7. Do not restart equipment until root cause is identified and written
   permit-to-restart is signed by shift supervisor and safety officer.

Incident Reporting:
All near-misses, first-aid cases, and injuries must be reported within
2 hours using the electronic HSMS form. Investigations follow the
5-Why root-cause analysis method within 24 hours of the incident.
""",

    "doc4_electrical_safety.txt": """\
Electrical Safety and Overheating Detection in Industrial Settings
==================================================================
Electrical hazards are among the leading causes of industrial fatalities.
Proper detection of overheating and implementation of electrical safety
measures are critical for both personnel safety and equipment reliability.

Overheating Detection Methods:
1. Infrared Thermography — scan energized equipment annually (or quarterly
   for critical systems). Temperature rise criteria per NETA MTS-2019:
   - ΔT 1–3°C vs. reference: monitor
   - ΔT 4–15°C: investigate within 3 months
   - ΔT 16–40°C: repair at next planned outage
   - ΔT >40°C: immediate action required
2. Thermal Sensors and RTDs — embed resistance temperature detectors in
   motor windings, transformer cores, and switchgear bus bars. Set DCS
   high-temperature alarms 10°C below trip setpoint.
3. Current Monitoring — sustained overcurrent causes I²R heating. Install
   electronic overload relays with phase imbalance detection; >5% imbalance
   accelerates insulation aging.
4. Ultrasonic Detection — partial discharge in high-voltage equipment
   produces ultrasonic emissions detectable with airborne ultrasound probes
   before thermal damage occurs.
5. Visual Inspection — look for discolored insulation, burnt smell,
   melted cable ties, or discoloration on terminal blocks.

Arc Flash Hazard Analysis:
Conduct arc flash study per IEEE 1584-2018. Label all panels with:
- Available fault current (kA)
- Arc flash boundary (cm)
- Incident energy (cal/cm²)
- Required PPE category

Ground Fault Protection:
Install GFCI on all 125V receptacles in wet or damp locations per NEC 210.8.
Use ground fault relays (87G) on medium-voltage equipment. Test GFCI
devices monthly by pressing the test button and verifying trip.

Cable Management and Insulation:
- Do not exceed 40% fill in conduit to prevent heat buildup.
- Derate cable ampacity when ambient temperature exceeds 30°C (NEC 310.15).
- Inspect cable insulation in high-flex applications (robot arms, cable
  chains) every 6 months for cracking or abrasion.
- Use LOTO before working on any energized circuit above 50V.
""",
}

# ---------------------------------------------------------------------------
# Query list
# ---------------------------------------------------------------------------
QUERIES = [
    "How to maintain industrial motors?",
    "What are common hydraulic system failures?",
    "What safety equipment is required on the factory floor?",
    "How to detect overheating in electrical equipment?",
    "What is the procedure for emergency shutdown?",
]

REQUERY_AFTER_ADD = "How to detect overheating in electrical equipment?"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SEP = "=" * 70
THIN = "-" * 70


def print_results(hits: list[dict], query: str, elapsed_ms: float) -> None:
    print(f"\n{SEP}")
    print(f"Query : {query}")
    print(f"Time  : {elapsed_ms:.1f} ms   |   Results: {len(hits)}")
    print(THIN)
    for i, h in enumerate(hits, 1):
        snippet = h["content"].replace("\n", " ")[:150]
        print(f"[{i}] score={h['score']:.4f}  source={h['source']}  chunk={h['chunk_id']}")
        print(f"    {snippet}...")
    print(SEP)


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------
def main() -> None:
    timings: list[tuple[str, float]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        # ----------------------------------------------------------------
        # Step 1 – Write test documents to disk
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 1 — Writing test documents")
        print(SEP)
        doc_paths = {}
        for name, content in DOCS.items():
            path = os.path.join(tmpdir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            doc_paths[name] = path
            print(f"  Written: {name}  ({len(content)} chars)")

        # ----------------------------------------------------------------
        # Step 2 – Initialise pipeline with isolated test collection
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 2 — Initialising RAGPipeline (collection: test_knowledge_base)")
        print(SEP)
        pipeline = RAGPipeline(collection_name="test_knowledge_base")

        # ----------------------------------------------------------------
        # Step 3 – Ingest 3 documents
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 3 — Ingesting 3 documents")
        print(SEP)
        initial_docs = ["doc1_motor_maintenance.txt",
                        "doc2_hydraulic_systems.txt",
                        "doc3_safety_protocols.txt"]
        for name in initial_docs:
            n = pipeline.ingest_file(doc_paths[name])
            print(f"  {name}: {n} chunks inserted")
        print(f"  Total chunks in collection: {pipeline.count()}")

        # ----------------------------------------------------------------
        # Step 4 – 5 queries with timing
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 4 — Running 5 queries")

        for q in QUERIES:
            t0 = time.perf_counter()
            hits = pipeline.query(q, top_k=5)
            elapsed = (time.perf_counter() - t0) * 1000
            timings.append((q, elapsed))
            print_results(hits, q, elapsed)

        # ----------------------------------------------------------------
        # Step 5 – Incremental add + re-query
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 5 — Incremental add: doc4_electrical_safety.txt")
        print(SEP)
        n = pipeline.add_documents([doc_paths["doc4_electrical_safety.txt"]])
        print(f"  Inserted {n} new chunks.  Total now: {pipeline.count()}")

        print("\n  Re-running query: \"How to detect overheating in electrical equipment?\"")
        t0 = time.perf_counter()
        hits_after = pipeline.query(REQUERY_AFTER_ADD, top_k=5)
        elapsed = (time.perf_counter() - t0) * 1000
        timings.append((f"[after add] {REQUERY_AFTER_ADD}", elapsed))
        print_results(hits_after, REQUERY_AFTER_ADD + " [after add]", elapsed)

        # ----------------------------------------------------------------
        # Step 6 – Timing summary
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 6 — Query timing summary")
        print(THIN)
        print(f"{'Query':<52}  {'ms':>8}")
        print(THIN)
        for label, ms in timings:
            truncated = label[:51]
            print(f"{truncated:<52}  {ms:>8.1f}")
        avg = sum(ms for _, ms in timings) / len(timings)
        print(THIN)
        print(f"{'Average':<52}  {avg:>8.1f}")
        print(SEP)

        # ----------------------------------------------------------------
        # Step 7 – Cleanup
        # ----------------------------------------------------------------
        print(f"\n{SEP}")
        print("STEP 7 — Dropping test collection")
        pipeline.drop_collection()
        print("  Done. Test collection removed.")
        print(SEP)

    print("\nAll tests completed.\n")


if __name__ == "__main__":
    main()
