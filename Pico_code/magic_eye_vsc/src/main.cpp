#include <Arduino.h>
#include <SPI.h>
#include "cloud_icon.h"


// Add these lines manually right here in main.cpp
#define CLOUD_W 200
#define CLOUD_H 200
extern const unsigned char cloud_mask[];
extern const unsigned char house_mask[];
// ── Pin definitions ───────────────────────────────────────────────────────────
#define A0    20
#define RESET 21
#define CS    22
#define BTN1  10
#define BTN2  11
#define BTN3  12
#define BTN4  13
#define BTN5  14
#define BTN6  15

// ── Display function declarations (defined in display.cpp) ────────────────────
void display_icon(uint16_t x, uint16_t y, uint16_t w, uint16_t h,
                  uint16_t icon_colour, uint16_t bg_colour,
                  const unsigned char* mask);
void display_begin(void);
void display_command(uint8_t cmd);
void display_data(uint8_t data);
void display_openAperture(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2);
void display_fillRectangle(uint16_t x1, uint16_t y1, uint16_t w, uint16_t h, uint16_t colour);
void display_putPixel(uint16_t x, uint16_t y, uint16_t colour);
uint16_t display_RGBToWord(uint16_t r, uint16_t g, uint16_t b);
void display_drawLine(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1, uint16_t Colour);
int32_t iabs(int32_t val);

// ── Lookup tables ─────────────────────────────────────────────────────────────
#define NUM_POINTS   155
#define MAX_BUFFER    16
#define EYE_START_DELIMITER  '['
#define EYE_END_DELIMITER    ']'
#define DIAL_START_DELIMITER '{'
#define DIAL_END_DELIMITER   '}'
#define SEPARATOR            ','
#define INPUT_QUERY          'I'
#define screen_radius        120
#define SPIDR ( *((volatile uint8_t *)0x4003c008))
#define SPISR ( *((volatile uint8_t *)0x4003c00c))

const int32_t xa1[]={0, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 38, 38, 38, 38, 38, 38, 38, 38, 38, 37, 37, 37, 37, 37, 37, 37, 36, 36, 36, 36, 36, 36, 36, 35, 35, 35, 35, 35, 34, 34, 34, 34, 34, 33, 33, 33, 33, 33, 32, 32, 32, 32, 31, 31, 31, 31, 30, 30, 30, 30, 29, 29, 29, 28, 28, 28, 28, 27, 27, 27, 26, 26, 26, 26, 25, 25, 25, 24, 24, 24, 23, 23, 23, 22, 22, 22, 21, 21, 21, 20, 20, 20, 19, 19, 19, 18, 18, 18, 17, 17, 17, 16, 16, 15, 15, 15, 14, 14, 14, 13, 13, 12, 12, 12, 11, 11, 11, 10, 10, 9, 9, 9, 8, 8, 7, 7, 7, 6, 6, 6, 5, 5, 4, 4, 4, 3, 3, 2, 2, 2, 1, 1};
const int32_t xa2[]={120, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119, 118, 118, 118, 118, 118, 118, 117, 117, 117, 117, 116, 116, 116, 115, 115, 115, 114, 114, 114, 113, 113, 113, 112, 112, 111, 111, 110, 110, 110, 109, 109, 108, 108, 107, 106, 106, 105, 105, 104, 104, 103, 102, 102, 101, 101, 100, 99, 99, 98, 97, 96, 96, 95, 94, 94, 93, 92, 91, 91, 90, 89, 88, 87, 86, 86, 85, 84, 83, 82, 81, 80, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 65, 64, 63, 62, 61, 60, 59, 58, 57, 56, 55, 54, 53, 52, 51, 50, 49, 47, 46, 45, 44, 43, 42, 41, 40, 38, 37, 36, 35, 34, 33, 32, 30, 29, 28, 27, 26, 25, 23, 22, 21, 20, 19, 18, 16, 15, 14, 13, 12, 10, 9, 8, 7, 6, 4, 3};
const int32_t ya1[]={0, 0, 0, 1, 1, 1, 2, 2, 3, 3, 3, 4, 4, 5, 5, 5, 6, 6, 7, 7, 7, 8, 8, 9, 9, 9, 10, 10, 11, 11, 11, 12, 12, 12, 13, 13, 14, 14, 14, 15, 15, 15, 16, 16, 17, 17, 17, 18, 18, 18, 19, 19, 19, 20, 20, 20, 21, 21, 21, 22, 22, 22, 23, 23, 23, 24, 24, 24, 25, 25, 25, 26, 26, 26, 26, 27, 27, 27, 28, 28, 28, 28, 29, 29, 29, 30, 30, 30, 30, 31, 31, 31, 31, 32, 32, 32, 32, 32, 33, 33, 33, 33, 34, 34, 34, 34, 34, 35, 35, 35, 35, 35, 36, 36, 36, 36, 36, 36, 36, 37, 37, 37, 37, 37, 37, 37, 38, 38, 38, 38, 38, 38, 38, 38, 38, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39};
const int32_t ya2[]={0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 19, 20, 21, 22, 23, 25, 26, 27, 28, 29, 30, 32, 33, 34, 35, 36, 37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 80, 81, 82, 83, 84, 85, 86, 86, 87, 88, 89, 90, 90, 91, 92, 93, 93, 94, 95, 96, 96, 97, 98, 98, 99, 100, 100, 101, 102, 102, 103, 104, 104, 105, 105, 106, 106, 107, 108, 108, 109, 109, 110, 110, 110, 111, 111, 112, 112, 113, 113, 113, 114, 114, 114, 115, 115, 115, 116, 116, 116, 117, 117, 117, 117, 118, 118, 118, 118, 118, 118, 119, 119, 119, 119, 119, 119, 119, 119, 119, 119};

// ── Globals ───────────────────────────────────────────────────────────────────
String   SerialIn;
String   cmd;
uint16_t colour;
uint16_t value;
uint16_t angle_index;
uint16_t target_index;
uint16_t fill_colour = 0;    // current fill colour — default black

// ─────────────────────────────────────────────────────────────────────────────
void setup()
{
    Serial.begin(115200);
    SPI.begin();
    SPI.beginTransaction(SPISettings(120000000, MSBFIRST, SPI_MODE3));
    pinMode(A0,    OUTPUT);
    pinMode(RESET, OUTPUT);
    pinMode(CS,    OUTPUT);
    pinMode(BTN1,  INPUT_PULLUP);
    pinMode(BTN2,  INPUT_PULLUP);
    pinMode(BTN3,  INPUT_PULLUP);
    pinMode(BTN4,  INPUT_PULLUP);
    pinMode(BTN5,  INPUT_PULLUP);
    pinMode(BTN6,  INPUT_PULLUP);
    display_begin();
        // Red
    display_fillRectangle(0, 0, 240, 240, display_RGBToWord(255, 0, 0));
    delay(1000);

    // Green
    display_fillRectangle(0, 0, 240, 240, display_RGBToWord(0, 255, 0));
    delay(1000);

    // Blue
    display_fillRectangle(0, 0, 240, 240, display_RGBToWord(0, 0, 255));
    delay(1000);

    // Clear to black
    display_fillRectangle(0, 0, 240, 240, display_RGBToWord(0, 0, 0));
    SerialIn    = "";
    colour      = display_RGBToWord(0, 192, 255);
    angle_index = 1;
    Serial.println("Ready. Commands: [blue]  [white]  [local]  [cloud]");
}

// ─────────────────────────────────────────────────────────────────────────────
void loop()
{
    char ch;

    // ── Draw lines up to target index ─────────────────────────────────────────
    while (angle_index < target_index)
    {
        if (angle_index > 0)
        {
            display_drawLine(xa1[angle_index]+screen_radius, screen_radius-ya1[angle_index],
                             screen_radius+xa2[angle_index], screen_radius-ya2[angle_index], colour);
            display_drawLine(xa1[angle_index]+screen_radius, screen_radius+ya1[angle_index],
                             screen_radius+xa2[angle_index], screen_radius+ya2[angle_index], colour);
            display_drawLine(screen_radius-xa2[angle_index], screen_radius-ya2[angle_index],
                             screen_radius-xa1[angle_index], screen_radius-ya1[angle_index], colour);
            display_drawLine(screen_radius-xa2[angle_index], screen_radius+ya2[angle_index],
                             screen_radius-xa1[angle_index], screen_radius+ya1[angle_index], colour);
        }
        angle_index++;
    }

    // ── Erase lines down to target index ──────────────────────────────────────
    while (angle_index > target_index)
    {
        angle_index--;
        display_drawLine(xa1[angle_index]+screen_radius, screen_radius-ya1[angle_index],
                         screen_radius+xa2[angle_index], screen_radius-ya2[angle_index], 0);
        display_drawLine(xa1[angle_index]+screen_radius, screen_radius+ya1[angle_index],
                         screen_radius+xa2[angle_index], screen_radius+ya2[angle_index], 0);
        display_drawLine(screen_radius-xa2[angle_index], screen_radius-ya2[angle_index],
                         screen_radius-xa1[angle_index], screen_radius-ya1[angle_index], 0);
        display_drawLine(screen_radius-xa2[angle_index], screen_radius+ya2[angle_index],
                         screen_radius-xa1[angle_index], screen_radius+ya1[angle_index], 0);
    }

    // ── Serial input handler ──────────────────────────────────────────────────
    if (Serial.available())
    {
        ch = ' ';
        Serial.readBytes(&ch, 1);

        // ── Button state query ────────────────────────────────────────────────
        if (ch == INPUT_QUERY)
        {
            uint32_t InputState = 0;
            if (digitalRead(BTN1) == 0) InputState |= (1 << 0);
            if (digitalRead(BTN2) == 0) InputState |= (1 << 1);
            if (digitalRead(BTN3) == 0) InputState |= (1 << 2);
            if (digitalRead(BTN4) == 0) InputState |= (1 << 3);
            if (digitalRead(BTN5) == 0) InputState |= (1 << 4);
            if (digitalRead(BTN6) == 0) InputState |= (1 << 5);
            Serial.print("I=");
            Serial.println(InputState);
        }

        // ── Eye command start ─────────────────────────────────────────────────
        else if (ch == EYE_START_DELIMITER)
        {
            SerialIn = "";
        }

        // ── Eye command end — parse command ───────────────────────────────────
        else if (ch == EYE_END_DELIMITER)
        {
            cmd = SerialIn.substring(0, SerialIn.indexOf(SEPARATOR));

            // ── [blue] ────────────────────────────────────────────────────────
            if (cmd == "blue")
            {
                Serial.println("blue command");
                fill_colour = display_RGBToWord(0, 0, 255);
                display_fillRectangle(0, 0, 240, 240, fill_colour);
                angle_index = target_index;
            }

            // ── [white] ───────────────────────────────────────────────────────
            else if (cmd == "white")
            {
                Serial.println("white command");
                fill_colour = display_RGBToWord(255, 255, 255);
                display_fillRectangle(0, 0, 240, 240, fill_colour);
                angle_index = target_index;
            }


            else if (cmd == "local")
        {
            display_icon(20, 20, CLOUD_W, CLOUD_H,
             display_RGBToWord(0, 150, 255),
             display_RGBToWord(0, 0, 0),
             house_mask);
}
            // ── [cloud] ───────────────────────────────────────────────────────
            else if (cmd == "cloud")
            {
                Serial.println("cloud command");
    display_icon(20, 20, CLOUD_W, CLOUD_H,
             display_RGBToWord(0, 150, 255),
             display_RGBToWord(0, 0, 0),
             cloud_mask);

    angle_index = target_index;
            }

            // ── Numeric colour,value command ──────────────────────────────────
            else
            {
                colour       = SerialIn.substring(0, SerialIn.indexOf(SEPARATOR)).toInt();
                value        = SerialIn.substring(SerialIn.indexOf(SEPARATOR) + 1).toInt();
                target_index = (value * sizeof(xa1) / 400);
                if (target_index == 0) target_index = 1;
                Serial.print("Colour = ");
                Serial.print(colour);
                Serial.print(", Value = ");
                Serial.print(value);
                Serial.print(", Target index = ");
                Serial.println(target_index);
            }

            SerialIn = "";
        }

        // ── Dial command start ────────────────────────────────────────────────
        else if (ch == DIAL_START_DELIMITER)
        {
            SerialIn = "";
        }

        // ── Dial command end ──────────────────────────────────────────────────
        else if (ch == DIAL_END_DELIMITER)
        {
            int32_t dialindex  = SerialIn.substring(0, SerialIn.indexOf(SEPARATOR)).toInt();
            int32_t dialcolour = SerialIn.substring(SerialIn.indexOf(SEPARATOR) + 1).toInt();
            Serial.print("dialindex = ");
            Serial.print(dialindex);
            Serial.print(", dialcolour = ");
            Serial.println(dialcolour, 16);
            SerialIn = "";
        }

        // ── Accumulate characters ─────────────────────────────────────────────
        else
        {
            SerialIn += ch;
        }
    }
}