cmake_minimum_required(VERSION 3.16)
project(lab1 CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(lab1
    main.cpp
    list.cpp
    DoublyLinkedList.h
    DynamicArray.h
    Stack.h
)
