#pragma once
#include <Arduino.h>

void display_begin(void);
void display_command(uint8_t cmd);
void display_data(uint8_t data);
void display_openAperture(uint16_t x1,uint16_t y1, uint16_t x2, uint16_t y2);
void display_fillRectangle(uint16_t x1, uint16_t y1, uint16_t w, uint16_t h, uint16_t colour);
void display_putPixel(uint16_t x, uint16_t y,uint16_t colour);
uint16_t display_RGBToWord(uint16_t r,uint16_t g,uint16_t b);
void display_icon(uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                  uint16_t icon_colour, uint16_t bg_colour,
                  const unsigned char* mask);