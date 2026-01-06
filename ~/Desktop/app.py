import pygame
import sys
import random

# Initialize pygame
pygame.init()

# Screen dimensions
WIDTH, HEIGHT = 800, 600
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Fun Pygame App")

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# Ball properties
ball_radius = 30
ball_pos = [WIDTH // 2, HEIGHT // 2]
ball_vel = [random.randint(-5, 5), random.randint(-5, 5)]
ball_color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

# Clock for controlling frame rate
clock = pygame.time.Clock()
FPS = 60

# Font for text
font = pygame.font.SysFont(None, 48)

def draw():
    global ball_pos, ball_vel, ball_color
    # Clear screen
    screen.fill(WHITE)
    # Draw ball
    pygame.draw.circle(screen, ball_color, ball_pos, ball_radius)
    # Update ball position
    ball_pos[0] += ball_vel[0]
    ball_pos[1] += ball_vel[1]
    # Bounce off walls
    if ball_pos[0] - ball_radius < 0 or ball_pos[0] + ball_radius > WIDTH:
        ball_vel[0] = -ball_vel[0]
    if ball_pos[1] - ball_radius < 0 or ball_pos[1] + ball_radius > HEIGHT:
        ball_vel[1] = -ball_vel[1]
    # Draw ball again after bounce
    pygame.draw.circle(screen, ball_color, ball_pos, ball_radius)
    # Render text
    text = font.render("Fun with Pygame!", True, BLACK)
    screen.blit(text, (WIDTH // 2 - text.get_width() // 2, HEIGHT - 50))
    # Update display
    pygame.display.flip()

# Main game loop
while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()
    draw()
    clock.tick(FPS)