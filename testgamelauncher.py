import pygame
import socket
import threading
import random
import math
import json
import time
import ast

# --- CONFIGURATION ---
WIDTH, HEIGHT = 800, 600
BLOCK_SIZE = 50
REACH = 150
COLORS = {1: (139, 69, 19), 2: (34, 139, 34), 3: (255, 215, 0), 4: (0, 191, 255)}

class TestGameLauncher:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Arial", 18)
        
        # Networking
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        x = input("Enter host ip (leave blank for localhost): ")
        self.server_addr = (x if x else 'localhost', 5555)

        self.BLOCK_HEALTH_MAX = {
            1: 100000,   # Dirt
            2: 100,    # Food
            3: 2000,   # Gold
            4: 1000010   # Diamond
        }

        self.last_collide = False

        # Tracks current damage to specific blocks: {(x, y): current_hp}
        self.active_mining = {}
        self.specs = False

        self.chat_log = []     # List of (time, "Name: Message")
        self.chat_input = ""
        self.is_typing = False

        self.view_mode = 0
        self.want_sprint = True

        self.last_key_time = 0
        self.key_delay = 0.5
        self.carrying_now = None
        
        # Player Stats
        self.pos = [0, 0]
        self.health, self.energy = 100.0, 100.0
        self.inventory = {1: 0, 2: 0, 3: 0, 4: 0}
        self.selected = 1
        self.name = input("Enter Name: ")
        self.strength = 10  # Starting damage
        self.xp = 0        # Progress toward next "Hittiness" level
        
        self.world = {} # Start empty, fill from server
        self.other_players = {}

    def save_data(self):
        x={str(k): v for k, v in self.active_mining.items()}
        data = {
            "pos": self.pos,
            "inventory": self.inventory,
            "health": self.health,
            "strength": self.strength,
            "active": x,
            "en": self.energy
        }
        with open(f"{self.name}_save.json", "w") as f:
            json.dump(data, f)

    def get_terminal_chat(self):
        msg = input("Enter chat: ")
        # Send it to the server immediately
        chat_packet = f"CHAT:{self.name}:{msg}"
        self.sock.sendto(chat_packet.encode(), self.server_addr)

    def load_data(self):
        try:
            with open(f"{self.name}_save.json", "r") as f:
                data = json.load(f)
                self.pos = data["pos"]
                self.inventory = {int(k): v for k, v in data["inventory"].items()}
                self.health = data["health"]
                self.strength = data["strength"]
                self.active_mining = {ast.literal_eval(k): v for k, v in data["active"].items()}
                self.energy = data["en"]
        except Exception as e:
            self.pos = [0, 0]
            self.health = 100
            self.energy = 100
            self.strength = 10
            self.xp = 0
            self.inventory = {1: 0, 2: 0, 3: 0, 4: 0} # 5 Dirt, 5 Food
            print("No save file found. Starting fresh! ", e)

    def network_thread(self):
        # Tell server we joined and need the map
        self.sock.sendto("REQUEST_MAP".encode(), self.server_addr)
        time.sleep(0.1)
        
        while True:
            try:
                my_data = f"{self.pos[0]},{self.pos[1]},{int(self.health)},{int(self.energy)},{self.name},{self.selected}"
                self.sock.sendto(my_data.encode(), self.server_addr)
                
                data, _ = self.sock.recvfrom(8192)
                raw = data.decode()

                if raw.startswith("PLACE:"):
                    parts = raw.split(":")
                    # parts[1]=gx, parts[2]=gy, parts[3]=bid
                    self.world[(int(parts[1]), int(parts[2]))] = int(parts[3])
                    continue
                
                # Handle initial map loading
                if raw.startswith("MAP_DATA:"):
                    _, gx, gy, bid = raw.split(":")
                    self.world[(int(gx), int(gy))] = int(bid)
                    continue

                if raw.startswith("URGENT_HIT:"):
                        damage = int(raw.split(":")[1])
                        self.health -= damage
                        print(f"DIRECT HIT RECEIVED! HP is now {self.health}")
                        continue # Skip the rest of the loop for this packet

                if raw.startswith("URGENT_CHAT:"):
                        user = raw.split(":")[1]
                        self.chat_log.append(user[1]+user[2])
                        print(f"DIRECT HIT RECEIVED! HP is now {self.health}")
                        continue # Skip the rest of the loop for this packet

                if raw.startswith("URGENT_GIVE:"):
                    item_received = int(raw.split(":")[1])
                    self.inventory[item_received] += 1
                    print(f"Received item {item_received} from another player!")

                p_part, w_part = raw.split("@")
                
                # Update Players
                new_others = {}
                for p in p_part.split("|"):
                    if "#" in p:
                        addr, d = p.split("#")
                        if addr != str(self.sock.getsockname()):
                            new_others[addr] = d.split(",")
                self.other_players = new_others

                # Update Events
                if w_part:
                    for change in w_part.split("/"):
                        p = change.split(":")
                        if p[0] == "MINE": self.world.pop((int(p[1]), int(p[2])), None)
                        if p[0] == "PLACE": self.world[(int(p[1]), int(p[2]))] = int(p[3])
                        if p[0] == "HIT" and p[1] == self.name:
                            self.health -= int(p[2])
            except: print("Network thread error:", e)

    def is_hovering(self, target_player_data, offset_x, offset_y):
        """
        target_player_data: The [x, y, hp, energy, name] list from the server
        offset_x/y: The current camera scroll values
        """
        # 1. Get current mouse position on your screen
        mouse_x, mouse_y = pygame.mouse.get_pos()

        # 2. Adjust mouse to World Coordinates (Reverse the Camera)
        world_mouse_x = mouse_x - offset_x
        world_mouse_y = mouse_y - offset_y

        # 3. Get Target's Hitbox (Top-left x,y to bottom-right x,y)
        target_x = float(target_player_data[0])
        target_y = float(target_player_data[1])
        
        # 4. Check if the mouse is inside the 30x30 box
        if (target_x <= world_mouse_x <= target_x + 30 and 
            target_y <= world_mouse_y <= target_y + 30):
            return True
            
        return False

    def run(self):
        threading.Thread(target=self.network_thread, daemon=True).start()
        last_save_time = time.time()
        self.load_data()
        self.last_eat_time = 0
        self.sock.sendto("ACTION:REQUEST_MAP".encode(), self.server_addr)
        
        while True:
            self.health -= 0.01 + math.log(self.strength*1, 1.5)/1000
            self.health = min(300,self.health) # We put it before regeneration so the player can get more than 300 health
            self.strength = max(0.1, self.strength - 0.0001)

            # Keep it from going below 0 manually to avoid weird UI bugs
            if self.health < 0:
                self.health = 0
            self.screen.fill((50, 50, 50))
            offset_x = WIDTH//2 - self.pos[0]
            offset_y = HEIGHT//2 - self.pos[1]
            current_time = time.time()
            if current_time - last_save_time > 10:  # Has 10 seconds passed?
                self.save_data()
                last_save_time = current_time

            # 1. Movement & Collision
            keys = pygame.key.get_pressed()
            shift_held = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
            current_time = time.time()
            move = [0, 0]

            if not self.is_typing:
                strength = min(self.strength/2 if self.want_sprint and self.strength > 10 else 5, (BLOCK_SIZE - 5)*10)*(0.7 if self.carrying_now else 1)
                weight = sum(self.inventory.values())
                strength = max(0, strength - weight/10)
                if keys[pygame.K_w]: move[1] -= strength
                if keys[pygame.K_s]: move[1] += strength
                if keys[pygame.K_a]: move[0] -= strength
                if keys[pygame.K_d]: move[0] += strength
                if keys[pygame.K_h]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        # Start the thread so it doesn't block the game
                        threading.Thread(target=self.get_terminal_chat, daemon=True).start()
                        self.last_key_time = current_time
                if keys[pygame.K_x]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        self.sock.sendto(f"ACTION:LEAVE_TAXI:{self.name}".encode(), self.server_addr)
                        self.last_key_time = current_time
                if keys[pygame.K_z]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        self.want_sprint = not self.want_sprint
                        self.last_key_time = current_time
                if keys[pygame.K_c]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        # Look through other players to find a passenger
                        for addr, p_data in self.other_players.items():
                            if self.is_hovering(p_data, offset_x, offset_y):
                                target_name = p_data[4]
                                if hasattr(self, 'carrying_now') and self.carrying_now == target_name:
                                    self.sock.sendto(f"ACTION:DROP:{target_name}".encode(), self.server_addr)
                                    self.carrying_now = None
                                else:
                                    my_data = f"{self.pos[0]},{self.pos[1]},{int(self.health)},{int(self.energy)},{self.name},{self.selected},{self.strength}"
                                    self.sock.sendto(f"ACTION:PICKUP:{my_data}".encode(), self.server_addr)
                                    self.carrying_now = target_name
                        self.last_key_time = current_time
                if keys[pygame.K_1]: self.selected = 1
                if keys[pygame.K_2]: self.selected = 2
                if keys[pygame.K_3]: self.selected = 3
                if keys[pygame.K_4]: self.selected = 4
                if keys[pygame.K_r]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        mouse_pos = pygame.mouse.get_pos()
                        # Adjusted mouse pos based on your camera offset
                        world_mouse_x = mouse_pos[0] - offset_x
                        world_mouse_y = mouse_pos[1] - offset_y

                        for addr, p in self.other_players.items():
                            p_x, p_y = float(p[0]), float(p[1])
                            # Check if mouse is inside the 30x30 player rectangle
                            if p_x < world_mouse_x < p_x + 30 and p_y < world_mouse_y < p_y + 30:
                                target_name = p[4]
                                item_id = self.selected
                                if self.inventory[item_id] > 0:
                                    # Send the transaction to the server
                                    give_msg = f"ACTION:GIVE:{target_name}:{item_id}"
                                    self.sock.sendto(give_msg.encode(), self.server_addr)
                                    self.inventory[item_id] -= 1 # Deduct locally
                                    self.last_key_time = current_time
                if keys[pygame.K_v]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        self.view_mode = (self.view_mode + 1)%4
                        modes = ["Show All", "Names Only", "Bodies Only", "Hide All"]
                        print(f"View Mode: {modes[self.view_mode]}")
                        self.last_key_time = current_time
                if keys[pygame.K_f]:
                    current_time = time.time()
                    if current_time - self.last_key_time > self.key_delay:
                        self.specs = not self.specs
                        self.last_key_time = current_time
                if keys[pygame.K_e]:
                    current_time = time.time()
                    if self.inventory[2] > 0 and current_time - self.last_eat_time > self.key_delay:
                        self.health = min(300, self.health + 20)
                        self.energy = min(100, self.energy + self.health//600)
                        self.last_eat_time = current_time
                        self.inventory[2] -= 1
            if move != [0,0] and self.energy - min(1, abs(move[0]/50) + abs(move[1]/50)) >= 0:
                new_x, new_y = self.pos[0] + move[0], self.pos[1] + move[1]
                can_move = True
                bid = 0
                gxy = ()
                # Check corners for collision
                for cx, cy in [(2,2), (28,2), (2,28), (28,28)]:
                    gx, gy = int((new_x + cx) // BLOCK_SIZE), int((new_y + cy) // BLOCK_SIZE)
                    if (gx, gy) in self.world:
                        can_move = False
                        pos = self.world[(gx, gy)]
                        gxy = (gx, gy)
                if can_move:
                    self.pos = [new_x, new_y]
                else:
                    self.health -= abs(move[0]/5) + abs(move[1]/5) if (not self.last_collide) and bid != 2 else 0
                self.energy = max(0, self.energy - min(1, abs(move[0]/50) + abs(move[1]/50))) if not self.last_collide else 0
                self.last_collide = not can_move
                if bid == 2 and (abs(move[1]/5) >= 20 or abs(move[0]/5) >= 20):
                    del self.world[gxy]
                if abs(move[1]/5) >= 50 or abs(move[0]/5) >= 50:
                    v = max(int(abs(move[1]/5)**2), int(abs(move[0]/5)**2))
                    
                    bgx = int(self.pos[0] // BLOCK_SIZE)
                    bgy = int(self.pos[1] // BLOCK_SIZE)
                    
                    radius = min(400, v-10)
                    
                    for gx in range(bgx - radius, bgx + radius):
                        for gy in range(bgy - radius, bgy + radius):
                            if (gx, gy) in self.world:
                                del self.world[(gx, gy)]
                                self.sock.sendto(f"ACTION:MINE:{gx}:{gy}".encode(), self.server_addr)
                                time.sleep(0.01)

                    kill_range = (v / 10) * BLOCK_SIZE
                    for player_id, p_data in self.other_players.items():
                        if abs(p_data['x'] - self.pos[0]) < kill_range and abs(p_data['y'] - self.pos[1]) < kill_range:
                            self.sock.sendto(f"ACTION:ATTACK:{player_id}:99".encode(), self.server_addr)
                            time.sleep(0.01)
            else:
                self.energy = min(100, self.energy + 0.6) # Fast Regen
            for (gx, gy) in list(self.active_mining.copy().keys()):
                if self.active_mining.get((gx, gy), None) >= self.BLOCK_HEALTH_MAX.get(self.world.get((gx, gy), 0), 0):
                    bid = self.world.pop((gx, gy), None) 
                    if bid is not None:
                        self.inventory[bid] += 1
                        self.sock.sendto(f"ACTION:MINE:{gx}:{gy}".encode(), self.server_addr)
                        self.energy -= 15
                        if bid == 3: self.strength += 2.0
                        if bid == 4 or bid == 1: self.strength += 10
                        else: self.strength += 0.1
                        self.health -= 5
            # 2. Events
            can_act = self.energy >= 30
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.save_data()
                    self.sock.sendto("ACTION:QUIT".encode(), self.server_addr)
                    return

                # --- 2. MOUSE CONTROLS (Mining & Placing) ---
                if event.type == pygame.MOUSEBUTTONDOWN and can_act:
                    mx, my = pygame.mouse.get_pos()
                    gx, gy = (mx - offset_x) // BLOCK_SIZE, (my - offset_y) // BLOCK_SIZE
                    
                    # Left Click: Mine or Attack (Unchanged)
                    if event.button == 1:
                        attacked = False
                        for addr, p in self.other_players.items():
                            p_rect = pygame.Rect(float(p[0])+offset_x, float(p[1])+offset_y, 30, 30)
                            if p_rect.collidepoint(mx, my):
                                if math.hypot(self.pos[0]-float(p[0]), self.pos[1]-float(p[1])) < REACH:
                                    self.sock.sendto(f"ACTION:HIT:{p[4]}:{self.strength}".encode(), self.server_addr)
                                    self.energy -= 30
                                    attacked = True
                                    self.xp += 2
                                    if self.xp >= self.strength*10: # Every set amount of hits, your damage increases
                                        self.strength += 2
                                        self.xp = 0
                                        print(f"LEVEL UP! Your damage is now {self.strength}")
                        if not attacked and (gx, gy) in self.world:
                            if self.active_mining.get((gx, gy), None) == None:
                                self.active_mining[(gx, gy)] = self.strength
                            else:
                                if self.active_mining.get((gx, gy), None) >= self.BLOCK_HEALTH_MAX.get(self.world.get((gx, gy), 0), 0):
                                    bid = self.world.pop((gx, gy), None) 
                                    if bid is not None:
                                        self.inventory[bid] += 1
                                        self.sock.sendto(f"ACTION:MINE:{gx}:{gy}".encode(), self.server_addr)
                                        self.energy -= 15
                                        self.strength += 0.5
                                else:
                                    self.active_mining[(gx, gy)] += self.strength

                    # Right Click: ONLY PLACES BLOCKS NOW
                    if event.button == 3:
                        if self.inventory[self.selected] > 0:
                            # You can now place food blocks as walls!
                            self.world[(gx, gy)] = self.selected
                            self.inventory[self.selected] -= 1
                            self.sock.sendto(f"ACTION:PLACE:{gx}:{gy}:{self.selected}".encode(), self.server_addr)
                            self.energy -= 30

            # 3. Draw
            for (gx, gy), bid in self.world.copy().items():
                pygame.draw.rect(self.screen, COLORS[bid], (gx*BLOCK_SIZE+offset_x, gy*BLOCK_SIZE+offset_y, BLOCK_SIZE-2, BLOCK_SIZE-2))
            for addr, p in self.other_players.items():
                p_x, p_y = float(p[0]) + offset_x, float(p[1]) + offset_y
                p_name = p[4]
                is_me = (p_name == self.name)

                # 1. Determine if we draw the Body (Mode 0 or 2)
                if self.view_mode in [0, 2] or is_me:
                    pygame.draw.rect(self.screen, (200, 0, 0), (p_x, p_y, 30, 30))

                # 2. Determine if we draw the Name/HP (Mode 0 or 1)
                if self.view_mode in [0, 1] or is_me:
                    safe_name = p_name[:15]
                    tag = self.font.render(f"{safe_name} ({p[2]})", True, (255, 255, 255))
                    self.screen.blit(tag, (p_x, p_y - 25))
            
            pygame.draw.rect(self.screen, (0, 255, 0), (WIDTH//2, HEIGHT//2, 30, 30))
            if not can_act:
                msg = self.font.render("LOW ENERGY", True, (255,0,0))
                self.screen.blit(msg, (WIDTH//2-40, HEIGHT//2+40))
            for (gx, gy), bid in self.world.copy().items():
                # Draw the block itself
                rect = (gx*BLOCK_SIZE + offset_x, gy*BLOCK_SIZE + offset_y, BLOCK_SIZE-2, BLOCK_SIZE-2)
                pygame.draw.rect(self.screen, COLORS[bid], rect)
                
                # NEW: Draw health bar if the block is being mined
                if (gx, gy) in self.active_mining:
                    current_hp = self.active_mining[(gx, gy)]
                    max_hp = self.BLOCK_HEALTH_MAX.get(bid, 1)
                    
                    # Calculate width of the bar (e.g., 40 pixels wide)
                    bar_width = 40
                    fill_width = int((current_hp / max_hp) * bar_width)
                    
                    # Draw background (red) and progress (green)
                    bar_x = gx*BLOCK_SIZE + offset_x + 5
                    bar_y = gy*BLOCK_SIZE + offset_y - 10
                    pygame.draw.rect(self.screen, (255, 0, 0), (bar_x, bar_y, bar_width, 5))
                    pygame.draw.rect(self.screen, (0, 155, 0), (bar_x, bar_y, fill_width, 5))
            
            ui = f"HP: {int(self.health)} | NRGY: {int(self.energy)} | Inv: {self.inventory} | Sel: {self.selected} | X: {self.pos[0]//10*10} | Y: {self.pos[1]//10*10}"
            ui2 = (f"{int(self.strength)} {self.want_sprint} {self.carrying_now} {self.last_collide} {self.name} {self.view_mode} {self.last_key_time}" if self.specs else "")
            self.screen.blit(self.font.render(ui, True, (255,255,255)), (10, 10))
            self.screen.blit(self.font.render(ui2, True, (255,255,255)), (10, 40))
            
            if self.health <= 0:
                print("YOU DIED!")
                
                # Calculate the grid position of the player
                gx, gy = int(self.pos[0] // BLOCK_SIZE), int(self.pos[1] // BLOCK_SIZE)
                
                # Clear local inventory (except starting items)
                self.inventory = {1: 0, 2: 0, 3: 0, 4: 0}
                self.energy, self.strength = 100, 10
                self.health, self.pos = 100, [0, 0] # Respawn
                self.sock.sendto(f"ACTION:DROP:{self.carrying_now}".encode(), self.server_addr)
                self.carrying_now = None
            pygame.display.flip()
            self.clock.tick(60)

if __name__ == "__main__":
    game = TestGameLauncher()
    game.run()
