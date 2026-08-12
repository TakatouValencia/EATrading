import sys
import os
from database import Database
from dotenv import load_dotenv

load_dotenv()

def fix_state():
    db = Database()
    signals = db.get_historical_signals(50)
    
    fixed_old = False
    cancelled_new = False
    
    for sig in signals:
        try:
            entry = float(sig.get('entry', 0))
            sig_id = sig.get('id')
            
            # Reactivate the 4385.58 signal
            if abs(entry - 4385.58) < 0.1:
                print(f"Reactivating old signal (ID: {sig_id}) at {entry} to ACTIVE")
                db.update_signal_status(sig_id, 'ACTIVE')
                fixed_old = True
                
            # Cancel the 4386.99 signal
            if abs(entry - 4386.99) < 0.1:
                print(f"Cancelling new unwanted signal (ID: {sig_id}) at {entry}")
                db.update_signal_status(sig_id, 'CANCELLED')
                cancelled_new = True
                
        except Exception as e:
            print(f"Error processing signal: {e}")
            
    if not fixed_old and not cancelled_new:
        print("Could not find the signals in the database. If you are using Supabase, make sure your environment variables are loaded.")
    else:
        print("Successfully updated the database!")

if __name__ == '__main__':
    fix_state()
