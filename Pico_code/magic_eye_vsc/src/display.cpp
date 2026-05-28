#include "display.h"
#include <SPI.h>

#define A0 20
#define RESET 21
#define CS 22
#define BTN1 10
#define BTN2 11
#define BTN3 12
#define BTN4 13
#define BTN5 14
#define BTN6 15

#define NUM_POINTS 155
#define MAX_BUFFER 16
#define EYE_START_DELIMITER '['
#define EYE_END_DELIMITER ']'
#define DIAL_START_DELIMITER '{'
#define DIAL_END_DELIMITER '}'
#define SEPARATOR ','
#define INPUT_QUERY 'I'
#define screen_radius 120

#define SPIDR ( *((volatile uint8_t *)0x4003c008))
#define SPISR ( *((volatile uint8_t *)0x4003c00c))

const int32_t xa1[]={0, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 38, 38, 38, 38, 38, 38, 38, 38, 38, 37, 37, 37, 37, 37, 37, 37, 36, 36, 36, 36, 36, 36, 36, 35, 35, 35, 35, 35, 34, 34, 34, 34, 34, 33, 33, 33, 33, 33, 32, 32, 32, 32, 31, 31, 31, 31, 30, 30, 30, 30, 29, 29, 29, 28, 28, 28, 28, 27, 27, 27, 26, 26, 26, 26, 25, 25, 25, 24, 24, 24, 23, 23, 23, 22, 22, 22, 21, 21, 21, 20, 20, 20, 19, 19, 19, 18, 18, 18, 17, 17, 17, 16, 16, 15, 15, 15, 14, 14, 14, 13, 13, 12, 12, 12, 11, 11, 11, 10, 10, 9, 9, 9, 8, 8, 7, 7, 7, 6, 6, 6, 5, 5, 4, 4, 4, 3, 3, 2, 2, 2, 1, 1};
const int32_t xa2[]={120, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 118, 118, 118, 118, 118, 118, 117, 117, 117, 117, 116, 116, 116, 115, 115, 115, 114, 114, 114, 113, 113, 113, 112, 112, 111, 111, 110, 110, 110, 109, 109, 108, 108, 107, 106, 106, 105, 105, 104, 104, 103, 102, 102, 101, 101, 100, 99, 99, 98, 97, 96, 96, 95, 94, 94, 93, 92, 91, 91, 90, 89, 88, 87, 86, 86, 85, 84, 83, 82, 81, 80, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65, 64, 63, 62, 61, 60, 59, 58, 57, 56, 55, 54, 53, 52, 51, 50, 49, 47, 46, 45, 44, 43, 42, 41, 40, 38, 37, 36, 35, 34, 33, 32, 30, 29, 28, 27, 26, 25, 23, 22, 21, 20, 19, 18, 16, 15, 14, 13, 12, 10, 9, 8, 7, 6, 4, 3 };
const int32_t ya1[]={0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 5, 5, 5, 6, 6, 7, 7, 7, 8, 8, 9, 9, 9, 10, 10, 11, 11, 11, 12, 12, 12, 13, 13, 14, 14, 14, 15, 15, 15, 16, 16, 17, 17, 17, 18, 18, 18, 19, 19, 19, 20, 20, 20, 21, 21, 21, 22, 22, 22, 23, 23, 23, 24, 24, 24, 25, 25, 25, 26, 26, 26, 26, 27, 27, 27, 28, 28, 28, 28, 29, 29, 29, 30, 30, 30, 30, 31, 31, 31, 31, 32, 32, 32, 32, 32, 33, 33, 33, 33, 34, 34, 34, 34, 34, 35, 35, 35, 35, 35, 36, 36, 36, 36, 36, 36, 36, 37, 37, 37, 37, 37, 37, 37, 38, 38, 38, 38, 38, 38, 38, 38, 38, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39};
const int32_t ya2[]={0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 19, 20, 21, 22, 23, 25, 26, 27, 28, 29, 30, 32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 80, 81, 82, 83, 84, 85, 86, 86, 87, 88, 89, 90, 90, 91, 92, 93, 93, 94, 95, 96, 96, 97, 98, 98, 99, 100, 100, 101, 102, 102, 103, 104, 104, 105, 105, 106, 106, 107, 108, 108, 109, 109, 110, 110, 110, 111, 111, 112, 112, 113, 113, 113, 114, 114, 114, 115, 115, 115, 116, 116, 116, 117, 117, 117, 117, 118, 118, 118, 118, 118, 118, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119};


void display_begin()
{
  // assuming SPI is already configured and up and running
  
	digitalWrite(A0,1);
	digitalWrite(CS,1);  
	delay(5);
	digitalWrite(RESET,0);
	delay(10);
	digitalWrite(RESET,1);
	digitalWrite(CS,0);
	digitalWrite(A0,0);
	delay(120);
	display_command(0xEF);

	display_command(0xEB);
	display_data(0x14);

	display_command(0xFE);
	display_command(0xEF);

	display_command(0xEB);
	display_data(0x14);

	display_command(0x84);
	display_data(0x40);

	display_command(0x85);
	display_data(0xFF);

	display_command(0x86);
	display_data(0xFF);

	display_command(0x87);
	display_data(0xFF);

	display_command(0x88);
	display_data(0x0A);

	display_command(0x89);
	display_data(0x21);

	display_command(0x8A);
	display_data(0x00);

	display_command(0x8B);
	display_data(0x80);

	display_command(0x8C);
	display_data(0x01);

	display_command(0x8D);
	display_data(0x01);

	display_command(0x8E);
	display_data(0xFF);

	display_command(0x8F);
	display_data(0xFF);


	display_command(0xB6);
	display_data(0x00);
	display_data(0x00);

	display_command(0x36);

	display_data(0x18);  // RGB format
	
	display_command(0x3A);
	display_data(0x05);

	display_command(0x90);
	display_data(0x08);
	display_data(0x08);
	display_data(0x08);
	display_data(0x08);

	display_command(0xBD);
	display_data(0x06);

	display_command(0xBC);
	display_data(0x00);

	display_command(0xFF);
	display_data(0x60);
	display_data(0x01);
	display_data(0x04);

	display_command(0xC3);
	display_data(0x13);
	display_command(0xC4);
	display_data(0x13);

	display_command(0xC9);
	display_data(0x22);

	display_command(0xBE);
	display_data(0x11);

	display_command(0xE1);
	display_data(0x10);
	display_data(0x0E);

	display_command(0xDF);
	display_data(0x21);
	display_data(0x0c);
	display_data(0x02);

	display_command(0xF0);
	display_data(0x45);
	display_data(0x09);
	display_data(0x08);
	display_data(0x08);
	display_data(0x26);
	display_data(0x2A);

	display_command(0xF1);
	display_data(0x43);
	display_data(0x70);
	display_data(0x72);
	display_data(0x36);
	display_data(0x37);
	display_data(0x6F);

	display_command(0xF2);
	display_data(0x45);
	display_data(0x09);
	display_data(0x08);
	display_data(0x08);
	display_data(0x26);
	display_data(0x2A);

	display_command(0xF3);
	display_data(0x43);
	display_data(0x70);
	display_data(0x72);
	display_data(0x36);
	display_data(0x37);
	display_data(0x6F);

	display_command(0xED);
	display_data(0x1B);
	display_data(0x0B);

	display_command(0xAE);
	display_data(0x77);

	display_command(0xCD);
	display_data(0x63);

	display_command(0x70);
	display_data(0x07);
	display_data(0x07);
	display_data(0x04);
	display_data(0x0E);
	display_data(0x0F);
	display_data(0x09);
	display_data(0x07);
	display_data(0x08);
	display_data(0x03);

	display_command(0xE8);
	display_data(0x34);

	display_command(0x62);
	display_data(0x18);
	display_data(0x0D);
	display_data(0x71);
	display_data(0xED);
	display_data(0x70);
	display_data(0x70);
	display_data(0x18);
	display_data(0x0F);
	display_data(0x71);
	display_data(0xEF);
	display_data(0x70);
	display_data(0x70);

	display_command(0x63);
	display_data(0x18);
	display_data(0x11);
	display_data(0x71);
	display_data(0xF1);
	display_data(0x70);
	display_data(0x70);
	display_data(0x18);
	display_data(0x13);
	display_data(0x71);
	display_data(0xF3);
	display_data(0x70);
	display_data(0x70);

	display_command(0x64);
	display_data(0x28);
	display_data(0x29);
	display_data(0xF1);
	display_data(0x01);
	display_data(0xF1);
	display_data(0x00);
	display_data(0x07);

	display_command(0x66);
	display_data(0x3C);
	display_data(0x00);
	display_data(0xCD);
	display_data(0x67);
	display_data(0x45);
	display_data(0x45);
	display_data(0x10);
	display_data(0x00);
	display_data(0x00);
	display_data(0x00);

	display_command(0x67);
	display_data(0x00);
	display_data(0x3C);
	display_data(0x00);
	display_data(0x00);
	display_data(0x00);
	display_data(0x01);
	display_data(0x54);
	display_data(0x10);
	display_data(0x32);
	display_data(0x98);

	display_command(0x74);
	display_data(0x10);
	display_data(0x85);
	display_data(0x80);
	display_data(0x00);
	display_data(0x00);
	display_data(0x4E);
	display_data(0x00);

	display_command(0x98);
	display_data(0x3e);
	display_data(0x07);

	display_command(0x35);
	display_command(0x21);

	display_command(0x11);
	delay(120);
	display_command(0x29);
	delay(20);

	display_command(0x2c);	
	display_fillRectangle(0,0,241,241,display_RGBToWord(0,0,0));
}
int32_t iabs(int32_t val)
{
	if (val < 0)
		val = -val;
	return val;
}
void display_command(uint8_t cmd)
{
  digitalWrite(A0,0);
  SPI.transfer(cmd);
}
void display_data(uint8_t cmd)
{
  digitalWrite(A0,1);
  SPI.transfer(cmd);
}
void display_openAperture(uint16_t x1,uint16_t y1, uint16_t x2, uint16_t y2)
{
  digitalWrite(A0,0);
  //SPI.transfer(0x2a);
  SPIDR=0x2a;
  digitalWrite(A0,1);
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(x1>>8);  
  //SPI.transfer((uint8_t)(x1>>8));
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(x1&0xff);
  //SPI.transfer((uint8_t)(x1&0xff));
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(x2>>8);
  //SPI.transfer((uint8_t)(x2>>8));
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(x2&0xff);
  //SPI.transfer((uint8_t)(x2&0xff));
  while((SPISR & 2)==0);
  digitalWrite(A0,0);
  SPIDR = (uint8_t)(0x2b);  
  while((SPISR & 2)==0);
  //SPI.transfer(0x2b);
  digitalWrite(A0,1);
  SPIDR = (uint8_t)(y1>>8);  
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(y1&0xff);
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(y2>>8);  
  while((SPISR & 2)==0);
  SPIDR = (uint8_t)(y2&0xff);  
  while((SPISR & 2)==0);
  //SPI.transfer((uint8_t)(y1>>8));
  //SPI.transfer((uint8_t)(y1&0xff));
  //SPI.transfer((uint8_t)(y2>>8));
  //SPI.transfer((uint8_t)(y2&0xff));
}
void display_fillRectangle(uint16_t x1, uint16_t y1, uint16_t w, uint16_t h, uint16_t colour)
{
  uint32_t pixelcount;
  display_openAperture(x1,y1,x1+w-1,y1+h-1);
  //display_command(0x2c);
  digitalWrite(A0,0);
  SPIDR = (uint8_t)(0x2c);  
  while((SPISR & 2)==0);
  digitalWrite(A0,1);
  pixelcount=w*h;
  while(pixelcount > 0)
  {
	SPIDR = (uint8_t)(colour>>8);  
  	while((SPISR & 2)==0);
	SPIDR = (uint8_t)(colour&8);  
  	while((SPISR & 2)==0);
    pixelcount--;
  }

}
void display_putPixel(uint16_t x, uint16_t y,uint16_t colour)
{
	display_openAperture(x,y,x+1,y+1);
	display_command(0x2c);
	digitalWrite(A0,1);
	SPI.transfer16(colour);
	delay(1);

}
uint16_t display_RGBToWord(uint16_t r,uint16_t g,uint16_t b)
{
	uint16_t rvalue;
	rvalue = (r & 0xf8) << 8;
	rvalue |= (g & 0xfc) << 3;
	rvalue |= (b >> 3);
	return rvalue;	
}
void display_drawLineLowSlope(uint16_t x0, uint16_t y0, uint16_t x1,uint16_t y1, uint16_t Colour)
{
    // Reference : https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm    
  int dx = x1 - x0;
  int dy = y1 - y0;
  int yi = 1;
  if (dy < 0)
  {
    yi = -1;
    dy = -dy;
  }
  int D = 2*dy - dx;
  
  int y = y0;

  for (int x=x0; x <= x1;x++)
  {
    //display_putPixel(x,y,Colour);    
	display_fillRectangle(x,y,1,1,Colour);
    if (D > 0)
    {
       y = y + yi;
       D = D - 2*dx;
    }
    D = D + 2*dy;
    
  }
}

void display_drawLineHighSlope(uint16_t x0, uint16_t y0, uint16_t x1,uint16_t y1, uint16_t Colour)
{
        // Reference : https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm

  int dx = x1 - x0;
  int dy = y1 - y0;
  int xi = 1;
  if (dx < 0)
  {
    xi = -1;
    dx = -dx;
  }  
  int D = 2*dx - dy;
  int x = x0;

  for (int y=y0; y <= y1; y++)
  {
    //display_putPixel(x,y,Colour);
	display_fillRectangle(x,y,1,1,Colour);
    if (D > 0)
    {
       x = x + xi;
       D = D - 2*dy;
    }
    D = D + 2*dx;
  }
}
void display_drawLine(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t Colour)
{
    // Reference : https://en.wikipedia.org/wiki/Bresenham%27s_line_algorithm
    if ( iabs(y1 - y0) < iabs(x1 - x0) )
    {
        if (x0 > x1)
        {
            display_drawLineLowSlope(x1, y1, x0, y0, Colour);
        }
        else
        {
            display_drawLineLowSlope(x0, y0, x1, y1, Colour);
        }
    }
    else
    {
        if (y0 > y1) 
        {
            display_drawLineHighSlope(x1, y1, x0, y0, Colour);
        }
        else
        {
            display_drawLineHighSlope(x0, y0, x1, y1, Colour);
        }
        
    }
}
void display_icon(uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                  uint16_t icon_colour, uint16_t bg_colour,
                  const unsigned char* mask)
{
    display_openAperture(x, y, x+w-1, y+h-1);
    digitalWrite(A0, 0);
    SPIDR = 0x2c;
    while((SPISR & 2) == 0);
    digitalWrite(A0, 1);
    uint32_t total = w * h;
    for (uint32_t i = 0; i < total; i++)
    {
        // extract the correct bit from the packed byte array
        uint8_t byte = pgm_read_byte(&mask[i / 8]);
        uint8_t bit  = (byte >> (7 - (i % 8))) & 1;
        uint16_t colour = bit ? icon_colour : bg_colour;
        SPIDR = (uint8_t)(colour >> 8);
        while((SPISR & 2) == 0);
        SPIDR = (uint8_t)(colour & 0xFF);
        while((SPISR & 2) == 0);
    }
}