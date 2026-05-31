#!/usr/bin/env python
"""Test script to diagnose Ladybug VECTOR extension loading."""

import sys
import tempfile


def test_ladybug_vector():
    """Test Ladybug vector extension installation and loading."""
    import ladybug

    print("=" * 80)
    print("Testing Ladybug VECTOR Extension")
    print("=" * 80)

    # Create a temporary database
    db_path = tempfile.mktemp(suffix=".db")
    print(f"\n1. Creating temporary database at: {db_path}")

    try:
        db = ladybug.Database(db_path)
        conn = ladybug.Connection(db)
        print("   ✓ Database connected")
    except Exception as e:
        print(f"   ✗ Failed to connect: {e}")
        return False

    # Try to install VECTOR extension
    print("\n2. Installing VECTOR extension...")
    try:
        result = conn.execute("CALL SHOW_OFFICIAL_EXTENSIONS() RETURN *;")
        print("   ✓ Official extensions:")
        print(result.get_as_df().to_string(index=False) if hasattr(result, "get_as_df") else result)
        result = conn.execute("INSTALL VECTOR;")
        print("   ✓ INSTALL VECTOR succeeded")
        print(f"      Result: {result}")
    except Exception as e:
        print(f"   ✗ INSTALL VECTOR failed: {e}")
        print(f"      Error type: {type(e).__name__}")

    # Try to load VECTOR extension
    print("\n3. Loading VECTOR extension...")
    try:
        result = conn.execute("LOAD VECTOR;")
        print("   ✓ LOAD VECTOR succeeded")
        print(f"      Result: {result}")
    except Exception as e:
        print(f"   ✗ LOAD EXTENSION VECTOR failed: {e}")
        print(f"      Error type: {type(e).__name__}")

    # List available extensions
    print("\n4. Listing available extensions...")
    try:
        result = conn.execute("CALL db_ext_info() RETURN *;")
        df = result.get_as_df() if hasattr(result, "get_as_df") else None
        if df is not None:
            print("   ✓ Extensions found:")
            print(df.to_string(index=False))
        else:
            print(f"   Result: {result}")
    except Exception as e:
        print(f"   ✗ Failed to list extensions: {e}")

    # Try to create a table with embedding column
    print("\n5. Creating test table with embedding column...")
    try:
        conn.execute("DROP TABLE IF EXISTS TestEmbedding;")
        conn.execute(
            """
            CREATE NODE TABLE TestEmbedding(
                id STRING,
                emb FLOAT[3],
                PRIMARY KEY(id)
            );
            """
        )
        print("   ✓ Table created with FLOAT[3] column")
    except Exception as e:
        print(f"   ✗ Failed to create table: {e}")
        return False

    # Insert test data
    print("\n6. Inserting test data...")
    try:
        conn.execute(
            """
            CREATE (n:TestEmbedding {
                id: 'test_1',
                emb: [1.0, 2.0, 3.0]
            });
            """
        )
        print("   ✓ Test data inserted")
    except Exception as e:
        print(f"   ✗ Failed to insert data: {e}")
        return False

    # Try to create vector index
    print("\n7. Creating vector index...")
    try:
        result = conn.execute(
            """
            CALL CREATE_VECTOR_INDEX(
                'TestEmbedding',
                'test_idx',
                'emb',
                mu := 30,
                ml := 60,
                pu := 0.05,
                metric := 'cosine',
                efc := 200,
                cache_embeddings := true
            );
            """
        )
        print("   ✓ Vector index created successfully")
        print(f"      Result: {result}")
        return True
    except Exception as e:
        print(f"   ✗ Failed to create vector index: {e}")
        print(f"      Error type: {type(e).__name__}")
        return False


if __name__ == "__main__":
    success = test_ladybug_vector()
    print("\n" + "=" * 80)
    if success:
        print("✓ Vector extension test PASSED")
        sys.exit(0)
    else:
        print("✗ Vector extension test FAILED")
        sys.exit(1)
