# To use this module, add the following to your MicroPython build command:
# USER_C_MODULES=../../../PB-1000_emu_AG2/src/micropython.cmake

add_library(hd61700_lib INTERFACE)

target_sources(hd61700_lib INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}/hd61700.c
    ${CMAKE_CURRENT_LIST_DIR}/modhd61700.c
    ${CMAKE_CURRENT_LIST_DIR}/lcd_controller.c
    ${CMAKE_CURRENT_LIST_DIR}/modlcd_controller.c
)

target_include_directories(hd61700_lib INTERFACE
    ${CMAKE_CURRENT_LIST_DIR}
)

# Link the library to the usermod target
target_link_libraries(usermod INTERFACE hd61700_lib)
