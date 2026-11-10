def migrate(cr, version):
    # Merge the former preparation stages without changing answers or signatures.
    cr.execute("""
        UPDATE araymond_maininvsession SET state = 'Preparation'
        WHERE full_flow = TRUE AND state IN ('Simulation', 'Risk')
    """)
