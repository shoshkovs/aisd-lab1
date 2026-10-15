#ifndef DYNAMIC_ARRAY_H
#define DYNAMIC_ARRAY_H

#include <string>
#include <iostream>
#include <cstdlib>

class DynamicArray {
    std::string* data_; // блок памяти под элементы
    int size_; // сколько реально лежит
    int capacity_;  // сколько влезает всего

public:
    DynamicArray() {
        data_ = nullptr;
        size_ = 0;
        capacity_ = 0;
    }

    ~DynamicArray() {
        delete[] data_;
    }

    void pushBack(std::string value) {
        if (size_ == capacity_) {
            int newCapacity;
            if (capacity_ == 0) {
                newCapacity = 4;
            } else {
                newCapacity = capacity_ * 2;
            }

            std::string* newData = new std::string[newCapacity];
            for (int i = 0; i < size_; i++) { // переносим старые элементы в новый блок памяти побольше 
                newData[i] = data_[i];
            }
            delete[] data_;
            data_ = newData;
            capacity_ = newCapacity;
        }
        data_[size_] = value;
        size_++;
    }

    std::string get(int index) {
        if (index < 0 || index >= size_) {
            std::cout << "Ошибка: выход за границы массива" << std::endl;
            exit(1);
        }
        return data_[index];
    }

    int size() {
        return size_;
    }

    bool empty() {
        return size_ == 0;
    }
};

#endif
