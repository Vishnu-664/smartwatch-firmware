import datetime
import time  
# CLOCK MODULE BY  DHAYABHARAN (ELC25006) AND SARAVANA(ELC25031)

class CLOCK:
    def __init__(self, toggleFlag, manual_offset=None):
        self.toggleFlag = toggleFlag
        self.manual_offset = manual_offset

    def MilitaryTime(self):
        while True:
            
            if self.manual_offset:
                current_time = datetime.datetime.now() + self.manual_offset
            else:
                current_time = datetime.datetime.now()
            
            if self.toggleFlag == 1:
                print(current_time.strftime("%H:%M:%S"), end="\r")
            elif self.toggleFlag == 0:
                print(current_time.strftime("%I:%M:%S %p"), end="\r")
            else:
                print("error")
                break
            
            time.sleep(1) 

    def Stopwatch(self):
        start_time = time.time()
        while True:
            elapsed = int(time.time() - start_time)
            hrs = elapsed // 3600
            mins = (elapsed % 3600) // 60
            secs = elapsed % 60
            print(f"Stopwatch -> {hrs:02d}:{mins:02d}:{secs:02d}", end="\r")
            time.sleep(1)

print("Menu:")
print("1 -> 24 hr format")
print("0 -> 12 hr format")
print("2 -> Stopwatch")
print("3 -> Set manual time")

def start():
    c = int(input("Enter choice: "))
    
    if c == 2:
        
        C = CLOCK(2)
        C.Stopwatch()
    elif c == 3:
        
        user_time_str = input("Enter start time (HH:MM:SS in 24-hr format): ")
        try:
            target_time = datetime.datetime.strptime(user_time_str, "%H:%M:%S").time()
            now = datetime.datetime.now()
            target_dt = datetime.datetime.combine(now.date(), target_time)
            offset = target_dt - now
            
            fmt = int(input("Choose display format for manual time (1 for 24-hr, 0 for 12-hr): "))
            C = CLOCK(fmt, manual_offset=offset)
            C.MilitaryTime()
        except ValueError:
            print("Invalid time format! Please use HH:MM:SS.")
    else:
        # Normal live clock mode
        C = CLOCK(c)
        C.MilitaryTime()

while True:
    try:
        start()
    except KeyboardInterrupt:
        print("\n--- Menu ---")
        print("choose 1 for 24 hr, 0 for 12 hr, 2 for stopwatch, 3 for manual time")