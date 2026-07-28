import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy.exc import IntegrityError

from src.db.engine import engine, SessionLocal
from src.domain.core import UserContext
from src.repositories.conversations import ConversationRepository

@pytest.fixture
def db_session():
    """
    Yields a session. We don't use transaction rollback here because we need 
    to test cross-thread row locks which require actual commits.
    Data is isolated by generating unique UUIDs for each test.
    """
    session = SessionLocal()
    yield session
    session.close()

def test_cross_tenant_id_behaves_as_not_found_and_raises_unique_violation(db_session):
    repo = ConversationRepository(db_session)
    
    # 1. Create a conversation for Tenant A
    user_a = UserContext(tenant_code="TENANT_A", user_id="user_a")
    conv_a = repo.get_or_create(None, user_a)
    db_session.commit()
    
    # 2. Try to get_or_create the SAME conversation ID but for Tenant B
    user_b = UserContext(tenant_code="TENANT_B", user_id="user_b")
    
    # Since it belongs to Tenant A, it will not be found for Tenant B.
    # get_or_create will attempt to create a NEW conversation using this same ID.
    # Postgres will reject this with a UniqueViolation (IntegrityError).
    with pytest.raises(IntegrityError):
        repo.get_or_create(conv_a.id, user_b)
        db_session.commit()
        
    db_session.rollback()


def test_concurrent_next_seq_for_update_serialises():
    # Setup: Create a conversation and commit it
    session = SessionLocal()
    repo = ConversationRepository(session)
    user = UserContext(tenant_code="TENANT_CONC", user_id="user_conc")
    conv = repo.get_or_create(None, user)
    session.commit()
    session.close()

    # The function that each thread will run
    def get_next_seq(conv_id):
        # Must use a separate session and connection for true concurrency
        thread_session = SessionLocal()
        thread_repo = ConversationRepository(thread_session)
        try:
            seq = thread_repo.next_seq_for_update(conv_id)
            thread_session.commit()
            return seq
        finally:
            thread_session.close()

    # Run 10 concurrent requests
    workers = 10
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(get_next_seq, conv.id) for _ in range(workers)]
        results = [f.result() for f in futures]

    # Verification: There should be exactly 'workers' unique sequences, from 1 to 10.
    assert len(results) == workers, "Should return a result for each thread"
    assert set(results) == set(range(1, workers + 1)), "Should produce exactly 1 to N with no duplicates or gaps"
